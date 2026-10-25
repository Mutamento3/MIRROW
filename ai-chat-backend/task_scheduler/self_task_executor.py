"""
Self-task executor — 官方 Flash + tool-calling 循环。

当 self_task 触发时，此模块:
1. 调官方 Flash（带 thinking）接收 execution_prompt + 工具描述
2. 从回复中提取主聊天共用的 @工具动作@（兼容旧 TOOL_CALL）
3. 执行工具 → 结果回注 → Flash follow-up
4. 最多 2 轮，防止幻觉累积
5. 返回自然语言消息供聊天注入

v2 复查管道升级时可直接复用 — 传入不同的 execution_prompt + tools 即可。
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .models import ScheduledTask
from shared_state import get_latest_persona_prompt as _get_latest_persona_prompt
from visual_dialogue import generate_visual_dialogue, tool_visual_images, apply_visual_receipts, fail_visual_receipts
from behavior_scheduler.tool_standardizer import ToolCallStandardizer
from tool_action_protocol import extract_tool_actions
from behavior_scheduler.sensory_execution import execute_tool_with_receipt as _execute_one_tool_with_receipt


def _latest_persona_prompt() -> str:
    try:
        return _get_latest_persona_prompt() or ""
    except Exception:
        return ""

logger = logging.getLogger(__name__)

# 最大 tool calling 轮次（硬上限，ScheduledTask.max_rounds 可下调但不可超过此值）
HARD_MAX_ROUNDS = 2


def _is_checkup_task(task: ScheduledTask) -> bool:
    """Keep a scheduled checkup distinct from ordinary delayed automation."""
    material = " ".join((task.title or "", task.execution_prompt or "", task.description or "", task.tool_name or "")).lower()
    return any(word in material for word in ("查岗", "检查状态", "看看人类伙伴", "睡了没", "在干嘛", "checkup"))


def _checkup_result(
    message: str, task: ScheduledTask, items: list[dict], thinking: str = "",
    model_source: dict | None = None,
) -> str | dict[str, Any]:
    if not _is_checkup_task(task):
        return {"message": message, "thinking": thinking, "model_source": model_source} if thinking or model_source else message
    items = _project_visual_items(items)
    ok = sum(1 for item in items if item.get("status") == "success")
    failed = sum(1 for item in items if item.get("status") in {"partial", "failure", "skipped"})
    if not ok:
        message = "这次查岗没有取得可确认的实时结果。"
        model_source = None
    elif not message.strip():
        message = "这次查岗取得了感官结果，但暂时没能生成可靠回复；详情请看查岗记录。"
    from wander_manager.checkup_receipts import scheduled_conclusion
    from checkup_assessment import source_status
    return {
        "message": message,
        "thinking": thinking,
        "model_source": model_source,
        "checkup": {
            "intent": "general",
            "origin": "scheduled",
            "conclusion": "uncertain" if failed else scheduled_conclusion(items),
            "source_status": source_status(items),
            "goal_assessment": {"status": "not_assessed"},
            "items": items,
            "summary": f"{ok}项成功" + (f"，{failed}项没看清" if failed else ""),
        },
    }


def _project_visual_items(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards show per-source evidence; phone state stays a separate fact."""
    from checkup_orchestrator import sensory_tool_items

    items = []
    for receipt in receipts:
        projected = sensory_tool_items([receipt])
        if not projected:
            items.append(receipt)  # Non-sensory failures still need their actual receipt.
            continue
        extra = receipt.get("extra_data") or {}
        sources = extra.get("sources") if receipt.get("tool") == "eyes" else extra.get("visual_sources")
        # Keep existing image-view links on scheduled cards; the shared evidence
        # projection itself contains only text/states, never image bytes.
        for source in sources if isinstance(sources, list) else []:
            if not isinstance(source, dict) or not source.get("attachment"):
                continue
            for item in projected:
                if item.get("tool") == source.get("source"):
                    item["attachment"] = source["attachment"]
        items.extend(projected)
    return items


async def execute_with_tools(
    task: ScheduledTask,
    *,
    tools: Dict[str, Any],
    call_pro_func: Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, Dict[str, Any]]],
    compile_action_func: Optional[Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, List[Dict[str, Any]]]]] = None,
    session_id: str = "",
) -> str | dict[str, Any]:
    """
    执行一个 self_task，带 tool-calling 能力。

    Args:
        task: task_type="self_task" 的 ScheduledTask
        tools: BaseTool 实例 dict（从 behavior_scheduler 传入，含正确 API keys）
        call_pro_func: 历史注入参数名；当前接官方 Flash async (messages) -> Dict
                       返回 {"content": str, "reasoning": str|None}
        compile_action_func: 主聊天的自然语言工具动作参数编译器
        session_id: 当前活跃会话 ID（用于上下文构建——AI 醒来时知道在聊什么）

    Returns:
        可直接注入聊天的自然语言消息。失败时返回 task.message_content 兜底。
    """
    prompt = task.execution_prompt or task.description or task.title
    max_rounds = min(task.max_rounds or HARD_MAX_ROUNDS, HARD_MAX_ROUNDS)

    if not tools:
        logger.warning(f"[SELF_TASK] 无可用工具，使用兜底消息: {task.title}")
        return _checkup_result(_fallback_message(task), task, [{"tool": "checkup", "description": "查岗工具", "status": "skipped", "error": "没有可用工具"}])

    receipts: list[dict[str, Any]] = []
    pending_visual: list[dict[str, Any]] = []
    reasoning_steps: list[str] = []
    executed_single_use: set[str] = set()
    correction_used = False
    standardizer = ToolCallStandardizer(tools)

    tool_desc = _build_tools_desc(tools)

    # ── 上下文：走 SELF_TASK recipe（身份/任务信息/工具描述/时间）──
    system_ctx = ""
    context_messages: List[Dict[str, Any]] = []
    task_info = f"标题:{task.title}\n计划:{task.execution_prompt or task.description or ''}"
    if task.reason:
        task_info += f"\n原因:{task.reason}"
    try:
        from context_builder import ContextBuilder
        result = await ContextBuilder.build("SELF_TASK",
            persona=_latest_persona_prompt or "",
            task_info=task_info,
            tools_desc=tool_desc,
            session_id=session_id,
        )
        system_ctx = result.system_content
        from context_builder.assembly import assemble_context_result
        context_messages = assemble_context_result(result)
    except Exception:
        pass
    if not context_messages:
        # 回退：身份 + 任务信息 + 工具描述
        persona = _latest_persona_prompt()
        system_ctx = f"{persona}\n\n任务:{task_info}\n\n可用工具:\n{tool_desc}"
        context_messages = [{"role": "system", "content": system_ctx}]

    # ── 自调度任务指令（user 层，不放在 system context 里）──
    from datetime import datetime
    task_instruction = (
        f"你是AI。你之前给自己安排了一个自动任务，现在时间到了。\n"
        f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"【工具调用格式】\n"
        f"在回复末尾单独一行写 @工具名 想做的动作@，例如 @eyes 看看人类伙伴周围@。\n\n"
        f"【规则】\n"
        f"1. 需要实时工具时，直接提交工具动作；动作前的预告不是工具调用\n"
        f"2. 工具结果返回后，基于真实结果自然回复人类伙伴\n"
        f"3. 不要编造结果\n"
        f"4. 最多调用 {max_rounds} 轮工具\n"
        f"5. 最终结合完整对话与真实结果，自然回复人类伙伴；逐来源详情由工具记录展示"
    )
    if _is_checkup_task(task):
        from checkup_orchestrator import CHECKUP_TOOL_GUIDANCE
        task_instruction += (
            f"\n6. 这是查岗：{CHECKUP_TOOL_GUIDANCE}"
            "本次需要实时核验时，首轮只提交感官工具动作；无法核验则明确说明。"
        )
    from context_builder.assembly import assemble_messages
    messages: List[Dict[str, Any]] = assemble_messages(
        history=context_messages,
        turn=[{"role": "user", "content": task_instruction}],
    )

    # 记录上下文调用供监控台展示（Builder 已自动记录，此处兜底）
    try:
        from context_builder import record_recipe_call
        rcl = record_recipe_call  # no-op if already recorded by Builder
    except Exception:
        pass

    # 查岗到点时由 AI 结合最新状态选择来源；旧任务留下的单工具计划不是执行命令。
    # 普通自我任务仍保留原有预执行合同。
    pre_tool_name = "" if _is_checkup_task(task) else (task.tool_name or "").strip()
    pre_tool_params = task.tool_params or {}
    if pre_tool_name and pre_tool_name not in tools:
        # 模糊匹配兜底：AI 写了 "camera" 但工具叫 "capture_camera"
        fuzzy = _fuzzy_match_tool(pre_tool_name, tools.keys())
        if fuzzy:
            print(f"[SELF_TASK] 模糊匹配: '{pre_tool_name}' → '{fuzzy}'", flush=True)
            pre_tool_name = fuzzy
    if pre_tool_name and pre_tool_name in tools:
        print(f"[SELF_TASK] 预执行指定工具: {pre_tool_name} params={pre_tool_params}", flush=True)
        result_content, receipt = await _execute_one_tool_with_receipt(tools, pre_tool_name, pre_tool_params)
        receipts.append(receipt)
        if tool_visual_images(receipt):
            pending_visual.append(receipt)
        # 直接注入工具结果到上下文，Pro 只需决定还要不要补调其他工具
        messages.append({
            "role": "system",
            "content": (
                f"[已自动执行 {pre_tool_name}，结果如下]\n{result_content}\n\n"
                f"请基于以上真实结果自然回复人类伙伴。如不需要更多工具，直接回复。"
            ),
        })
        max_rounds = 1  # 预执行后只给 1 轮，Pro 可选择补调一个工具或直接 NL

    for round_num in range(1, max_rounds + 1):
        print(f"[SELF_TASK] Round {round_num}/{max_rounds} for '{task.title}'", flush=True)
        while True:
            try:
                if pending_visual:
                    response = await _complete_visual_receipts(pending_visual, messages, prompt)
                    pending_visual.clear()
                else:
                    response = await call_pro_func(messages)
            except Exception as e:
                logger.error("[SELF_TASK] Pro 调用失败 (round %s): %s", round_num, e)
                response = {"content": "", "finish_reason": "error"}

            reasoning = str(response.get("reasoning") or "").strip()
            if reasoning:
                reasoning_steps.append(reasoning)
            content = str(response.get("content") or "").strip()
            tool_calls = response.get("tool_calls") or await _extract_task_tool_calls(content, tools, compile_action_func)
            if tool_calls or receipts or not _is_checkup_task(task) or correction_used:
                break
            # A prose-only preamble is not a checkup. Give AI one bounded
            # chance to submit an actual action before recording failure.
            correction_used = True
            logger.warning(
                "[SELF_TASK] 查岗首轮未提交工具动作 (round=%s, content_len=%s, finish_reason=%s)",
                round_num, len(content), response.get("finish_reason"),
            )
            if content:
                messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "system",
                "content": "尚未收到可执行工具动作，刚才的话没有触发查岗。"
                           "如需核验，请在回复末尾提交 @工具名 想做的动作@；"
                           "若无法核验，请明确说明。",
            })

        if not tool_calls:
            if not content:
                logger.warning(
                    "[SELF_TASK] Flash 返回空内容 (round=%s, finish_reason=%s)",
                    round_num, response.get("finish_reason"),
                )
                if receipts:
                    break
            if _is_checkup_task(task) and not receipts:
                receipts.append(_missing_action_receipt("模型未提交可执行感官动作"))
            return _checkup_result(
                _clean_content(content, tools) or _fallback_message(task),
                task, receipts, "\n\n".join(reasoning_steps),
                response.get("model_source") if _clean_content(content, tools).strip() else None,
            )

        feedback: list[str] = []
        messages.append({"role": "assistant", "content": (
            json.dumps(response["vision_envelope"], ensure_ascii=False)
            if response.get("vision_envelope") else content
        )})
        for tool_call in tool_calls:
            standardized = standardizer.standardize(tool_call)
            if not standardized.is_valid:
                feedback.append(f"工具请求无效，未执行：{standardized.error_message}")
                if _is_checkup_task(task):
                    receipts.append(_missing_action_receipt(
                        f"工具请求无效：{standardized.error_message}",
                        tool=str(tool_call.get("tool") or "checkup")[:80],
                    ))
                continue
            t_name, t_params = standardized.tool_name, standardized.parameters
            if t_name not in tools:
                feedback.append(f"工具 {t_name} 不在本任务的可用范围内，未执行。")
                if _is_checkup_task(task):
                    receipts.append(_missing_action_receipt(
                        "工具不在本任务的可用范围内", tool=t_name[:80],
                    ))
                continue
            if getattr(tools[t_name], "single_use", False) and t_name in executed_single_use:
                feedback.append(f"工具 {t_name} 本任务已执行过，没有重复采集。")
                continue

            result_content, receipt = await _execute_one_tool_with_receipt(tools, t_name, t_params)
            if getattr(tools[t_name], "single_use", False):
                executed_single_use.add(t_name)
            receipts.append(receipt)
            if tool_visual_images(receipt):
                pending_visual.append(receipt)
            feedback.append(f"[工具 {t_name} 的执行结果]\n{result_content}")

        messages.append({
            "role": "system",
            "content": "\n\n".join(feedback)
                       + "\n请结合完整对话与真实结果自然回复人类伙伴；逐来源详情由工具记录展示。"
                         "如仍需未使用的感官，可继续提交工具动作。",
        })

    # 达到上限 → 最后一轮 Pro 不要求工具，直接生成回复
    messages.append({
        "role": "system",
        "content": "已达到最大轮次。请基于所有工具结果，对人类伙伴说一段自然的话，不再提交工具动作。",
    })
    try:
        final_response = (
            await _complete_visual_receipts(pending_visual, messages, prompt, allow_tools=False)
            if pending_visual else await call_pro_func(messages)
        )
        final_content = (final_response.get("content") or "").strip()
        if final_content:
            final_reasoning = str(final_response.get("reasoning") or "").strip()
            if final_reasoning:
                reasoning_steps.append(final_reasoning)
            return _checkup_result(
                _clean_content(final_content, tools), task, receipts, "\n\n".join(reasoning_steps),
                final_response.get("model_source"),
            )
    except Exception as e:
        logger.error(f"[SELF_TASK] 最终 Pro 调用失败: {e}")

    if _is_checkup_task(task) and not receipts:
        receipts.append(_missing_action_receipt("模型未提交可执行感官动作"))
    return _checkup_result(_fallback_message(task), task, receipts, "\n\n".join(reasoning_steps))


def _build_tools_desc(tools: Dict[str, Any]) -> str:
    """构建紧凑工具描述（供 Pro 系统提示词）。"""
    lines = []
    for name, tool in tools.items():
        schema = getattr(tool, 'parameters_schema', {}) or {}
        props = schema.get("properties", {})
        param_str = ", ".join(props.keys()) if props else "无参数"
        short_desc = (tool.description or "")[:80].replace("\n", " ")
        lines.append(f"- {name}: {short_desc}（参数: {param_str}）")
    return "\n".join(lines)


def _fuzzy_match_tool(name: str, candidates: Any) -> Optional[str]:
    """
    模糊匹配工具名。AI 写了 "camera" 但工具叫 "capture_camera" → 子串匹配兜底。
    返回匹配到的工具名，无匹配返回 None。
    """
    name_lower = name.strip().lower()
    if not name_lower:
        return None

    # 1) 子串匹配：name 是候选名的子串 或 候选名包含 name
    for c in candidates:
        c_lower = c.lower()
        if name_lower in c_lower or c_lower in name_lower:
            return c

    # 2) rapidfuzz (OB_Rev 已有依赖)，不可用时静默跳过
    try:
        from rapidfuzz import fuzz
        best = None
        best_score = 0
        for c in candidates:
            score = fuzz.ratio(name_lower, c.lower())
            if score > best_score:
                best_score = score
                best = c
        if best and best_score >= 75:
            return best
    except ImportError:
        pass

    return None


# ── TOOL_CALL 解析 ─────────────────────────────────────────

async def _extract_task_tool_calls(
    content: str,
    tools: Dict[str, Any],
    compile_action_func: Optional[Callable[[List[Dict[str, Any]]], Coroutine[Any, Any, List[Dict[str, Any]]]]],
) -> list[dict[str, Any]]:
    """Use the chat action protocol, retaining legacy JSON for saved prompts."""
    directives = extract_tool_actions(content, tools)
    if directives:
        actions = [{"tool": item.tool, "instruction": item.instruction} for item in directives]
        if compile_action_func:
            try:
                compiled = await compile_action_func(actions)
            except Exception as exc:
                logger.warning("[SELF_TASK] 工具动作参数编译失败: %s", exc)
                return []
        else:
            # Offline callers can still execute parameterless actions and
            # single free-text fields. Production uses the shared compiler.
            compiled = []
            for action in actions:
                schema = getattr(tools[action["tool"]], "parameters_schema", {}) or {}
                properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
                instruction = action["instruction"]
                params: dict[str, Any] = {}
                if instruction and len(properties) == 1:
                    key, definition = next(iter(properties.items()))
                    if definition.get("type") == "string" and not definition.get("enum"):
                        params[key] = instruction
                compiled.append({"tool": action["tool"], "parameters": params})
        if not isinstance(compiled, list) or len(compiled) != len(actions):
            logger.warning("[SELF_TASK] 工具动作参数编译数量与动作不一致")
            return []
        if any(
            not isinstance(call, dict) or call.get("tool") != action["tool"]
            for call, action in zip(compiled, actions)
        ):
            logger.warning("[SELF_TASK] 工具动作参数编译改动了工具选择，拒绝执行")
            return []
        return compiled
    legacy = _extract_tool_call(content)
    return [legacy] if legacy else []


def _missing_action_receipt(reason: str, *, tool: str = "checkup") -> dict[str, Any]:
    return {
        "tool": tool, "description": "查岗动作", "status": "failure",
        "result": "", "error": reason[:300],
    }

def _extract_tool_call(content: str) -> Optional[Dict[str, Any]]:
    """
    从 Pro 回复中提取 TOOL_CALL。
    格式: TOOL_CALL: {"tool":"...","parameters":{...}}
    兼容 TOOL_CALL： (全角冒号) 和多行 JSON。
    """
    # 正则快速路径
    pattern = r'TOOL_CALL\s*[:：]\s*(\{[^}]+\})'
    match = re.search(pattern, content)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 括号匹配兜底（处理嵌套 JSON）
    idx = content.find("TOOL_CALL")
    if idx >= 0:
        after = content[idx + len("TOOL_CALL"):]
        colon = after.find(":") if ":" in after else after.find("：")
        if colon >= 0:
            after = after[colon + 1:]
        start = after.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(after)):
                if after[i] == "{":
                    depth += 1
                elif after[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(after[start:i + 1])
                        except json.JSONDecodeError:
                            break
    return None


async def _execute_one_tool(tools: Dict[str, Any], tool_name: str, params: Dict[str, Any]) -> str:
    """执行单个工具，返回结果文本。失败时返回错误描述。"""
    content, _ = await _execute_one_tool_with_receipt(tools, tool_name, params)
    return content


async def _complete_visual_receipts(
    receipts: list[dict[str, Any]], messages: list[dict[str, Any]], task_prompt: str,
    *, allow_tools: bool = True,
) -> dict[str, Any]:
    """Analyze the whole tool batch once, retaining history and native actions."""
    images = [image for receipt in receipts for image in tool_visual_images(receipt)]
    try:
        result = await generate_visual_dialogue(
            messages, images, turn_text=f"到点任务：{task_prompt}", allow_tools=allow_tools,
        )
        apply_visual_receipts(receipts, result["vision_envelope"])
        return result
    except Exception:
        fail_visual_receipts(receipts)
        # Provider failures are not fabricated dialogue or action decisions.
        return {"content": "", "finish_reason": "error", "tool_calls": []}


def _clean_content(text: str, tools: Optional[Dict[str, Any]] = None) -> str:
    """Keep model-only tool syntax out of chat and persistent history."""
    from visible_reply import clean_visible_reply
    return clean_visible_reply(text, known_tool_names=tuple(tools or ()))


def _fallback_message(task: ScheduledTask) -> str:
    """兜底消息。"""
    return task.message_content or task.description or f"⏰ {task.title}（自动任务已完成）"
