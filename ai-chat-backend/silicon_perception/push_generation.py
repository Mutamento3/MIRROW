"""哨兵主动消息生成边界。

接收结构化感知决定，组装 SENTINEL_PUSH Recipe，并调用消息 Flash。
主程序只负责注入人格、Affect v2 上下文、会话和 LLM 函数。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict
from body_admission import ordinary
from silicon_perception.analysis.sentinel_grounding import (
    GroundingResult,
    SentinelFactContract,
    grounding_repair_prompt,
    natural_care_fallback,
    natural_care_motivation,
)

logger = logging.getLogger(__name__)

_GENERATION_TURN = (
    "【MIRROW 后端哨兵主动生成请求；API user 仅为传输载体】\n"
    "本轮由感知事件触发，人类伙伴没有新增聊天发言。"
    "上面的对话是带说话人和时间的历史；AI 的旧消息与摘要是 AI 的来源，不是人类伙伴的回复。"
    "请根据本次感知与联系动机，生成 AI 主动发出的自然短消息。\n"
    "【后端生成请求结束】"
)


def normalize_sentinel_decision(decision_input: Any) -> Dict[str, Any]:
    """兼容旧字符串输入，统一为可审计的主动联系决定。"""
    if isinstance(decision_input, dict):
        return {
            "observation": str(decision_input.get("observation") or "").strip(),
            "should_push": bool(decision_input.get("should_push", True)),
            "push_motivation": str(decision_input.get("push_motivation") or "").strip(),
            "user_context": str(decision_input.get("user_context") or "").strip(),
            "fact_contract": decision_input.get("fact_contract"),
            "grounding_rejections": list(
                decision_input.get("grounding_rejections")
                or decision_input.get("rejection_reasons")
                or []
            ),
            'verified_history_evidence': list(decision_input.get('verified_history_evidence') or []),
        }
    return {
        "observation": str(decision_input or "").strip(),
        "should_push": True,
        "push_motivation": "这个变化让你产生了主动联系人类伙伴的念头。",
        "user_context": "",
        "fact_contract": None,
        "grounding_rejections": [],
    }


@ordinary
async def generate_sentinel_push(
    decision_input: Any,
    *,
    persona: str,
    mood: str,
    session_id: str,
    call_flash: Callable[[list[dict[str, str]]], Awaitable[Any]],
    expression_tools: dict | None = None,
) -> dict:
    """生成一条哨兵主动消息，保留消息 Flash 的 reasoning。"""
    decision = normalize_sentinel_decision(decision_input)
    contract = SentinelFactContract.from_dict(decision.get("fact_contract"))
    should_push = bool(decision.get("should_push", True))
    summary = decision["observation"]
    motivation = decision["push_motivation"]
    input_grounding = contract.validate_text(f"{summary}\n{motivation}")
    grounding_rejections = list(input_grounding.rejections)
    if not input_grounding.accepted:
        # The first layer may have been delayed or its contract may have been
        # superseded by a newer location. Do not carry an old sensor detail
        # into the second layer; preserve the contact intent only.
        summary = natural_care_fallback(should_push=should_push)
        if not contract.validate_text(motivation).accepted:
            motivation = natural_care_motivation(should_push=should_push)
    fact_context_text = contract.to_context_text()
    frozen_state = contract.redact_untrusted_location_context(decision["user_context"])
    built = None
    try:
        from context_builder import ContextBuilder

        built = await ContextBuilder.build(
            "SENTINEL_PUSH",
            persona=persona,
            mood=mood,
            # Affect remains globally shadowed while it is evaluated, but the
            # user explicitly wants the sentinel path to see AI's current
            # source-backed self/resonance state.  Scope the override to this
            # recipe instead of changing every Context Builder consumer.
            affect_context_mode="enabled",
            sentinel_summary=summary,
            sentinel_push_motivation=motivation,
            sentinel_history_evidence=decision.get('verified_history_evidence', []),
            push_current_state_text=frozen_state,
            context_query=summary,
            session_id=session_id,
        )
        from context_builder.assembly import assemble_context_result
        messages = assemble_context_result(built)
        if fact_context_text:
            # This is an objective late-bound observation block, separate from
            # Context Builder ingredients and from the output guard contract.
            messages.append({"role": "system", "content": fact_context_text})
        all_context = "\n".join(str(item.get("content", "")) for item in messages)
        print(
            f"[SENTINEL-DIAG] summary={summary[:100]!r} | "
            f"注入={'你通过硅基感知' in all_context} | ctx_len={len(all_context)}",
            flush=True,
        )
    except Exception:
        messages = [{
            "role": "system",
            "content": (
                f"{persona or '你是AI。'}\n\n硅基感知系统注意到：{summary}"
                "\n\n你刚注意到上述变化，并准备主动给人类伙伴发一条短消息。"
                f"\n[哨兵第一层的主观联系念头，非历史事实权威]：{motivation}"
                f"\n\n{fact_context_text}"
            ),
        }]

    from proactive_band_touch import output_instruction
    from proactive_checkup import (
        current_schedule_context, output_instruction as checkup_instruction, split_output,
    )
    messages.append({"role": "system", "content": current_schedule_context()})
    messages.append({"role": "system", "content": output_instruction(additional_action=True) + checkup_instruction()})
    if expression_tools:
        from proactive_expression import output_instruction as expression_instruction
        messages.append({"role": "system", "content": expression_instruction(expression_tools)})

    def split_actions(text):
        if expression_tools:
            from proactive_expression import split_output as split_expression
            return split_expression(text)
        visible, band, checkup, error = split_output(text)
        return visible, band, checkup, [], error

    messages.append({"role": "user", "content": _GENERATION_TURN})
    result = await call_flash(messages)
    raw_generated_content = (
        str(result.get("content") or "").strip()
        if isinstance(result, dict)
        else str(result or "").strip()
    )
    generated_content, band_touch_request, checkup_request, expression_requests, action_parse_error = split_actions(
        raw_generated_content
    )
    from visible_reply import clean_visible_reply
    generated_content = clean_visible_reply(generated_content)
    reasoning = result.get("reasoning") if isinstance(result, dict) else None
    model_source = result.get("model_source") if isinstance(result, dict) else None

    # The same contract is applied to the natural message. A repair can happen
    # once, and only the final accepted content is committed to context.
    output_grounding = contract.validate_text(generated_content)
    if generated_content and not output_grounding.accepted:
        grounding_rejections.extend(output_grounding.rejections)
        repair = grounding_repair_prompt(
            layer="推送", original=generated_content, contract=contract,
            rejections=output_grounding.rejections,
        )
        try:
            repaired_result = await call_flash(
                list(messages) + [{"role": "user", "content": (
                    "【MIRROW 后端事实校正请求；不是人类伙伴的新发言】\n"
                    + repair + "\n【后端校正请求结束】"
                )}]
            )
            repaired_raw_content = (
                str(repaired_result.get("content") or "").strip()
                if isinstance(repaired_result, dict)
                else str(repaired_result or "").strip()
            )
            repaired_content, repaired_band_request, repaired_checkup_request, repaired_expressions, repaired_action_error = split_actions(
                repaired_raw_content
            )
            repaired_content = clean_visible_reply(repaired_content)
            repaired_grounding = contract.validate_text(repaired_content)
        except Exception as repair_error:
            logger.warning("Sentinel push grounding repair failed: %s", repair_error)
            repaired_content = ""
            repaired_grounding = GroundingResult(accepted=False, rejections=[])
        if repaired_content and repaired_grounding.accepted:
            generated_content = repaired_content
            band_touch_request = repaired_band_request
            checkup_request = repaired_checkup_request
            expression_requests = repaired_expressions
            action_parse_error = repaired_action_error
            reasoning = repaired_result.get("reasoning") if isinstance(repaired_result, dict) else None
            model_source = repaired_result.get("model_source") if isinstance(repaired_result, dict) else None
        else:
            if repaired_grounding.rejections:
                grounding_rejections.extend(repaired_grounding.rejections)
            generated_content = natural_care_fallback(should_push=should_push)
            model_source = None
            band_touch_request = None
            checkup_request = None
            expression_requests = []
            action_parse_error = ""
    elif not generated_content:
        # Empty provider output (including reasoning/action-only output) is not
        # a generated message. Callers already require content before sending.
        # Keep the fact-conflict repair fallback above as its separate contract.
        logger.warning("Sentinel push returned no visible text; skipping message and actions")
        generated_content = ""
        band_touch_request = None
        checkup_request = None
        expression_requests = []
        action_parse_error = ""
    if built is not None and generated_content:
        try:
            from context_builder import commit_context_result
            commit_context_result(built)
        except Exception:
            pass
    if isinstance(result, dict):
        return {
            "content": generated_content,
            "session_id": session_id,
            "expression_requests": expression_requests,
            "reasoning": reasoning,
            "model_source": model_source,
            "should_push": should_push,
            "push_motivation": motivation,
            "band_touch_request": band_touch_request,
            "band_touch_parse_error": action_parse_error,
            "checkup_request": checkup_request,
            "checkup_parse_error": action_parse_error,
            "grounding_rejections": [item.to_dict() for item in grounding_rejections],
            "rejection_reasons": [item.to_dict() for item in grounding_rejections],
        }
    return {
        "content": generated_content,
        "session_id": session_id,
        "expression_requests": expression_requests,
        "reasoning": reasoning,
        "should_push": should_push,
        "push_motivation": motivation,
        "band_touch_request": band_touch_request,
        "band_touch_parse_error": action_parse_error,
        "checkup_request": checkup_request,
        "checkup_parse_error": action_parse_error,
        "grounding_rejections": [item.to_dict() for item in grounding_rejections],
        "rejection_reasons": [item.to_dict() for item in grounding_rejections],
    }
