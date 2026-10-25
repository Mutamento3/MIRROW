"""Bounded, source-backed day material for the V3 read-only pilot.

An active day is a scheduling boundary, not evidence that a behavior happened on
that calendar date. Each item retains its authoritative timestamp and source ID.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

from memory_v2.conversation_source import ConversationMessage, is_canonical_session_id
from memory_v2.replay import estimate_tokens


MAX_CONTEXT_CHARS = 160
PILOT_INPUT_TOKEN_BUDGET = 12000
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)"
    r"\s*[:=]\s*[^\s,;]+|\bBearer\s+[A-Za-z0-9._~+/=-]+"
    r"|\bsk-[A-Za-z0-9_-]{12,}\b|(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"
    r"|data:[^;,\s]+;base64,[A-Za-z0-9+/=]+"
)


def contains_sensitive_text(value: str) -> bool:
    """Fail closed for obvious credentials/network literals in extra pilot calls."""
    return bool(_SENSITIVE_TEXT.search(value or ""))


@dataclass(frozen=True)
class ContextSource:
    message_id: str
    content_digest: str
    timestamp: str


@dataclass(frozen=True)
class DayItem:
    message_id: str
    timestamp: str
    calendar_date: str
    text: str
    preceding_k: str = ""
    session_id: str = ""
    active_date: str = ""
    context_source: ContextSource | None = None
    context_truncated: bool = False

    def model_view(self, number: int) -> dict:
        return {"n": number, "time": self.timestamp, "human": self.text,
                "preceding_k": self.preceding_k,
                "preceding_k_time": self.context_source.timestamp if self.context_source else "",
                "preceding_k_truncated": self.context_truncated}


@dataclass(frozen=True)
class DayBatch:
    items: tuple[DayItem, ...]
    estimated_tokens: int

    def model_view(self) -> list[dict]:
        return [item.model_view(index) for index, item in enumerate(self.items, 1)]


def select_day_items(messages: Sequence[ConversationMessage], *, session_id: str,
                     active_date: str) -> tuple[DayItem, ...]:
    """Keep human speech plus small antecedent context, never proactive AI speech.

    The AI snippet helps interpret elliptical replies, but model observations
    must cite an exact substring of the human message, not this snippet.
    """
    if not is_canonical_session_id(session_id):
        raise ValueError("habit pilot requires the canonical private session")
    result: list[DayItem] = []
    seen: set[str] = set()
    preceding_k = ""
    context_source = None
    context_truncated = False
    for message in messages:
        if message.session_id != session_id or message.active_date != active_date:
            continue
        if message.role == "assistant" and message.source_kind == "chat" and not message.event_type:
            full_context = message.content.strip()
            candidate = full_context[-MAX_CONTEXT_CHARS:]
            preceding_k = "" if contains_sensitive_text(candidate) else candidate
            context_source = (ContextSource(message.message_id,
                                            hashlib.sha256(full_context.encode("utf-8")).hexdigest(),
                                            message.timestamp)
                              if preceding_k and message.message_id else None)
            if context_source is None:
                preceding_k = ""
            context_truncated = bool(preceding_k) and len(full_context) > MAX_CONTEXT_CHARS
            continue
        if (message.role != "user" or message.source_kind != "chat" or message.event_type
                or not message.message_id or message.message_id in seen
                or not message.content.strip() or contains_sensitive_text(message.content)):
            continue
        seen.add(message.message_id)
        result.append(DayItem(message_id=message.message_id, timestamp=message.timestamp,
                              calendar_date=message.calendar_date,
                              text=message.content.strip(), preceding_k=preceding_k,
                              session_id=message.session_id, active_date=message.active_date,
                              context_source=context_source, context_truncated=context_truncated))
    return tuple(result)


def item_token_estimate(item: DayItem) -> int:
    # Include JSON field/sequence overhead, not just raw human text.
    return estimate_tokens(json.dumps(item.model_view(1), ensure_ascii=False)) + 4


def batch_items(items: Sequence[DayItem], *, max_estimated_tokens: int = PILOT_INPUT_TOKEN_BUDGET
                ) -> tuple[DayBatch, ...]:
    """Split by input budget only; no ordinary/heavy-day message-count label."""
    if max_estimated_tokens < 1000:
        raise ValueError("input budget too small")
    batches: list[DayBatch] = []
    current: list[DayItem] = []
    used = 0
    for item in items:
        cost = item_token_estimate(item)
        if cost > max_estimated_tokens:
            raise ValueError("one source message exceeds the pilot input budget")
        if current and used + cost > max_estimated_tokens:
            batches.append(DayBatch(tuple(current), used))
            current, used = [], 0
        current.append(item)
        used += cost
    if current:
        batches.append(DayBatch(tuple(current), used))
    return tuple(batches)


def safe_plan(active_date: str, items: Sequence[DayItem], batches: Sequence[DayBatch]) -> dict:
    """Text-free report: never print transcript, context, IDs, or sensor values."""
    return {"active_date": active_date, "human_messages": len(items),
            "human_chars": sum(len(item.text) for item in items),
            "antecedent_context_chars": sum(len(item.preceding_k) for item in items),
            "estimated_input_tokens": sum(batch.estimated_tokens for batch in batches),
            "batch_count": len(batches),
            "batches": [{"messages": len(batch.items), "estimated_input_tokens": batch.estimated_tokens}
                        for batch in batches]}
