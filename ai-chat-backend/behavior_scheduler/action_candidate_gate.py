"""Cheap semantic-family prefilter for the existing Flash tool fallback.

The gate never chooses or executes a tool.  It only identifies a bounded set
of turns where the normal Pro reply may have omitted a real-world action.  The
Flash extractor remains the semantic authority and all existing
standardization, permission, platform, deduplication and execution checks stay
downstream.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


_ACTION = (
    r"查(?:一下|查看|查查)?|看(?:一下|一眼|看)?|搜(?:一下|搜)?|找(?:一下)?|"
    r"记(?:一下|下来|一笔)?|设(?:一下|一个)?|删(?:一下|掉)?|取消|修改|改一下|"
    r"发(?:一下|一条)?|画(?:一下|一张)?|生成|放(?:一下|一首)?|播(?:一下)?|"
    r"停(?:一下|掉)?|调(?:一下)?|测(?:一下)?|拍(?:一下|一张)?|截(?:一下|个图)?|"
    r"打开|创建|执行|操作|处理|查岗"
)

_EXPLICIT_DELEGATION = re.compile(
    rf"(?:帮我|替我|给我|你来|你去|麻烦你|能不能|可不可以|可以帮我|来[，, ]*|这就|马上)"
    rf".{{0,10}}(?:{_ACTION})",
    re.IGNORECASE,
)
_IMPERATIVE_ACTION = re.compile(
    rf"^(?:再|继续|重新|直接|现在|立刻|马上)?\s*(?:{_ACTION})",
    re.IGNORECASE,
)
_REALTIME_EVIDENCE = re.compile(
    r"(?:现在|此刻|刚刚|目前).{0,14}(?:在干嘛|做什么|有没有|是不是|还在|睡了|醒着|"
    r"心率|心跳|电量|屏幕|手机|电脑|位置|状态)|"
    r"(?:睡了没|醒了没|在干嘛|还玩手机吗|还在用电脑吗)",
    re.IGNORECASE,
)
_FUTURE_ACTION = re.compile(
    rf"(?:\d+|一|两|二|三|几|半)\s*(?:秒|分钟|小时|天)后.{{0,18}}(?:{_ACTION}|查岗|提醒)|"
    rf"(?:待会儿?|等会儿?|过会儿?|晚点|稍后|明天|今晚|下次).{{0,18}}(?:{_ACTION}|查岗|提醒)|"
    rf"(?:{_ACTION}|查岗|提醒).{{0,18}}(?:\d+|一|两|二|三|几|半)\s*(?:秒|分钟|小时|天)后",
    re.IGNORECASE,
)
_ASSISTANT_COMMITMENT = re.compile(
    rf"(?:我(?:去|来|先|这就|马上|现在|待会|等会|晚点)|这就|马上).{{0,12}}(?:{_ACTION}|查岗)",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"^(?:那就|那再|再来|再试|继续|重来|重新来|换一个|换一张|同样的|照刚才|按刚才|"
    r"还是那个|再弄|再搞|再查|再看|再发|再画|再放)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ActionCandidateDecision:
    should_review: bool
    reasons: tuple[str, ...] = ()


def _has_tool_attachment(item: Mapping[str, Any]) -> bool:
    for key in ("tool_calls", "toolCalls"):
        value = item.get(key)
        if isinstance(value, (list, dict)) and bool(value):
            return True
        if isinstance(value, str) and value.strip() not in {"", "[]", "{}", "null", "None"}:
            try:
                if bool(json.loads(value)):
                    return True
            except Exception:
                return True
    if str(item.get("tool_summary") or item.get("toolSummary") or "").strip():
        return True
    return False


def has_recent_tool_history(history: Sequence[Mapping[str, Any]] | None, limit: int = 8) -> bool:
    """Whether a recent recorded message carries an actual tool attachment."""
    if not history:
        return False
    return any(_has_tool_attachment(item) for item in list(history)[-limit:] if isinstance(item, Mapping))


def detect_action_candidate(
    *,
    user_text: str = "",
    assistant_text: str = "",
    reasoning_tail: str = "",
    conversation_history: Sequence[Mapping[str, Any]] | None = None,
) -> ActionCandidateDecision:
    """Return bounded reasons for running the existing Flash intent extractor.

    This deliberately avoids bare verbs such as ``看``/``想``/``要``.  A false
    positive costs one Flash review, while the Flash layer must still return no
    call for discussion, hypotheticals and already-satisfied requests.
    """
    user = str(user_text or "").strip()
    assistant = str(assistant_text or "").strip()
    reasoning = str(reasoning_tail or "").strip()
    reasons: list[str] = []

    if _EXPLICIT_DELEGATION.search(user) or _IMPERATIVE_ACTION.search(user):
        reasons.append("explicit_delegation")
    if _REALTIME_EVIDENCE.search(user):
        reasons.append("realtime_evidence_gap")
    if _FUTURE_ACTION.search(user):
        reasons.append("future_action")
    if _ASSISTANT_COMMITMENT.search(assistant) or _ASSISTANT_COMMITMENT.search(reasoning):
        reasons.append("assistant_commitment")
    if _CONTINUATION.search(user) and has_recent_tool_history(conversation_history):
        reasons.append("recent_tool_continuation")

    return ActionCandidateDecision(bool(reasons), tuple(dict.fromkeys(reasons)))
