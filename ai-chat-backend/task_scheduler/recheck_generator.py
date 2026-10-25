"""查岗/提醒复查的 LLM 消息生成器。

任务调度器只关心复查阶段和结果；本模块负责把这些事实组织成
``REMINDER_PUSH`` 上下文并调用轻量模型，避免启动主文件承载生成细节。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def create_recheck_llm_generator(
    *,
    persona_provider: Callable[[], Optional[str]],
    session_id_provider: Callable[[], Optional[str]],
):
    """创建与 ``TaskManager`` 回调签名一致的复查生成器。"""

    async def generate(
        original_message: str,
        task_title: str,
        reason: str,
        ai_context: str,
        stage: int,
        reminder_type: str = "task",
    ) -> dict[str, str]:
        extra = ""
        if reason:
            extra += f"\n设置此提醒的原因：{reason}"
        if ai_context:
            extra += f"\n上下文：{ai_context}"

        if reminder_type == "inconsistency":
            task_info = f"查岗复查（第{stage}次）：你之前去问了：\"{original_message}\"{extra}"
            scenario = "查岗"
        else:
            task_info = f"提醒复查（第{stage}次）：你之前发了提醒：\"{original_message}\"（标题：{task_title}）{extra}"
            scenario = ""

        # 失败时仍保留人设和复查事实，不能因为上下文构建失败而丢失人格连续性。
        try:
            persona_text = persona_provider() or ""
        except Exception:
            persona_text = ""
        messages = []
        built_result = None
        try:
            from context_builder import ContextBuilder

            build_kwargs = {
                "persona": persona_text,
                "mood": "",
                "task_info": task_info,
                "context_query": task_info,
                "session_id": session_id_provider() or "",
            }
            if scenario:
                build_kwargs["_scenario"] = scenario
            result = await ContextBuilder.build("REMINDER_PUSH", **build_kwargs)
            from context_builder.assembly import assemble_context_result
            messages = assemble_context_result(result)
            built_result = result
        except Exception as exc:
            logger.warning(f"复查 REMINDER_PUSH 构建失败，回退裸拼: {exc}")

        if not messages:
            persona = f"\n你的人设：{persona_text}" if persona_text else ""
            messages = [{
                "role": "system",
                "content": f"{persona}{extra}\n{task_info}",
            }]

        if reminder_type == "inconsistency":
            if stage == 1:
                tone = "带一点在意和小情绪——已经给了她一次机会还没回，像是在等她解释"
                action = "再问一次，语气带点小委屈"
            elif stage == 2:
                tone = "略微失落但不过分——像是在心里记了一笔，语气有小埋怨但不指责"
                action = "最后问一次"
            else:
                tone = "把查岗结果自然地告诉她，带一点在意和小情绪，但不要质问"
                action = "把这次查岗结果说出来"
            prompt = f"""你注意到人类伙伴的行为和她说的话不一致，刚才去问了："{original_message}"
她{'还没回复' if stage == 1 else '两次都没回复' if stage == 2 else '在后续查岗流程中仍没有回复'}。请以第一人称自然地{action}。
语气要求：{tone}
生成30-80字消息，直接输出，不要引号和标记。"""
        else:
            if stage == 1:
                tone = '温和警告——轻轻提醒她，带一点「我在等你回复」的暗示，不要催促感'
                action = '温和地再提醒一次'
                situation = "她还没回复"
            elif stage == 2:
                tone = "记账语气——略微不满但绝不指责，像是在心里的小账本上记了一笔，语气可以带点小傲娇"
                action = "最后试一次"
                situation = "她两次提醒都没回复"
            else:
                tone = "自然、在意，但不要指责或宣称记账"
                action = "在她按掉手机闹钟五分钟仍未回复后补充提醒一次"
                situation = "她按掉了手机闹钟，但之后五分钟仍没有回复"
            prompt = f"""你之前给人类伙伴发了提醒："{original_message}"（标题：{task_title}）
{situation}。请以第一人称自然地{action}。
语气要求：{tone}
生成30-80字消息，直接输出，不要引号和标记。"""

        from proactive_band_touch import output_instruction, split_output
        messages.append({
            "role": "system",
            "content": prompt + "\n" + output_instruction(),
        })
        from llm_client import call_llm_api_flash
        from neuron_registry import neuron_trace

        with neuron_trace("reminder_recheck_generator", recipe="REMINDER_PUSH", model="Flash") as trace:
            trace.set_input(messages)
            response = await call_llm_api_flash(messages, temperature=0.7, enable_thinking=True, usage_tag="reminder_recheck")
            raw_message = (
                str(response.get("content") or "")
                if isinstance(response, dict)
                else str(response or "")
            )
            ai_message, band_request, band_parse_error = split_output(raw_message)
            reasoning = str(response.get("reasoning") or "") if isinstance(response, dict) else ""
            trace.set_output(ai_message)

        if built_result is not None:
            try:
                from context_builder import commit_context_result
                commit_context_result(built_result)
            except Exception:
                pass

        from context_builder.proactive_output import clean_proactive_output

        return {
            "message": clean_proactive_output(ai_message) or f"刚才提醒你 {task_title}，别忘了哦~",
            "thinking": reasoning.strip(),
            "model_source": response.get("model_source") if isinstance(response, dict) and ai_message.strip() else None,
            "band_touch_request": band_request,
            "band_touch_parse_error": band_parse_error,
        }

    return generate
