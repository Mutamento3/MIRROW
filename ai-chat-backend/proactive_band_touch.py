"""Shared one-shot band action boundary for autonomous AI messages.

Wander, Sentinel and Reminder each generate their own natural message. This
module owns their optional band action; Wander and Sentinel may also schedule
a later checkup through a separate one-shot action boundary.
"""

from __future__ import annotations

import json
from typing import Any, Optional


TOOL_NAME = "band_touch"


def output_instruction(*, additional_action: bool = False) -> str:
    return (
        "\n你可以自主选择是否用手环吸引人类伙伴注意；这是可选的小手段，不必每次使用。"
        "不用时只输出消息正文。使用时只在正文末尾另起一行追加一次："
        "纯震动整行写 @band_touch@；附纸条整行写 @band_touch 纸条正文@，"
        "纸条正文放在两个 @ 之间，送出后同时震动。"
        + ("其他可用动作见后续工具合同。" if additional_action else "除此之外没有可用工具。")
        + "动作请求不等于执行成功，正文不得声称已经写入或人类伙伴已经看到。"
    )


def split_output(content: str) -> tuple[str, Optional[dict[str, Any]], str]:
    """Split one allowlisted tail action from visible proactive copy.

    The natural ``@band_touch ...@`` form is authoritative.  The former JSON
    form remains input-compatible so an in-flight/older model response cannot
    leak into the visible message during deployment.
    """
    raw = str(content or "")
    from tool_action_protocol import extract_tool_actions, strip_tool_protocol_text

    directives = extract_tool_actions(raw, (TOOL_NAME,))
    if directives:
        visible = strip_tool_protocol_text(raw, (TOOL_NAME,))
        if len(directives) != 1:
            return visible, None, "multiple_actions"
        instruction = directives[0].instruction.strip()
        params = {"note": instruction} if instruction else {}
        return visible, {"tool": TOOL_NAME, "parameters": params}, ""

    marker = "TOOL_CALL:"
    index = raw.rfind(marker)
    if index < 0:
        return raw.strip(), None, ""
    message = raw[:index].strip()
    payload = raw[index + len(marker):].strip()
    try:
        parsed, end = json.JSONDecoder().raw_decode(payload)
    except (json.JSONDecodeError, TypeError):
        return message, None, "invalid_action_json"
    if payload[end:].strip():
        return message, None, "multiple_or_trailing_actions"
    if not isinstance(parsed, dict) or parsed.get("tool") != TOOL_NAME:
        return message, None, "tool_not_allowed"
    return message, parsed, ""


async def execute_request(
    tool: Any,
    request: Optional[dict[str, Any]],
    parse_error: str = "",
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Validate and execute one non-retrying UI-only band request."""
    if parse_error:
        return None, {"requested": True, "status": "rejected", "error": parse_error}
    if request is None:
        return None, {"requested": False, "status": "not_requested"}
    if tool is None:
        return None, {"requested": True, "status": "unavailable"}

    from behavior_scheduler.tool_standardizer import ToolCallStandardizer

    standardized = ToolCallStandardizer({TOOL_NAME: tool}).standardize(request)
    if not standardized.is_valid:
        return None, {
            "requested": True,
            "status": "rejected",
            "error": standardized.error_message,
        }
    params = standardized.parameters
    description = tool.get_user_facing_description(**params)
    try:
        result = await tool.execute(**params)
        status = getattr(getattr(result, "status", None), "value", "error")
        success = status == "success"
        record = {
            "tool": TOOL_NAME,
            "parameters": params,
            "description": description,
            "result": str(getattr(result, "content", "") or ""),
            "success": success,
            "status": "success" if success else "failure",
            "delivery": tool.get_result_delivery(**params),
        }
        if getattr(result, "error", None):
            record["error"] = str(result.error)
        if getattr(result, "extra_data", None):
            record["extra_data"] = result.extra_data
        return record, {"requested": True, "status": record["status"]}
    except Exception as exc:
        record = {
            "tool": TOOL_NAME,
            "parameters": params,
            "description": description,
            "result": "手环回执中断，执行结果未知，未自动重试",
            "success": False,
            "status": "failure",
            "delivery": tool.get_result_delivery(**params),
            "error": type(exc).__name__,
        }
        return record, {
            "requested": True,
            "status": "failure",
            "error": type(exc).__name__,
        }


__all__ = ["TOOL_NAME", "execute_request", "output_instruction", "split_output"]
