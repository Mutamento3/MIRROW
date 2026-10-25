"""Read-only recent dialogue support, separate from the authoritative query.

The automatic path uses context_text for corroboration, not independent lexical
matching or entity scope. Legacy explicit-reference variants remain available
to callers that intentionally request query expansion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from behavior_scheduler.bookshelf_search import sanitize_archive_text

from .conversation_source import ConversationMessage


_REFERENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("plural", re.compile(r"那(?:两|2)(?:个|件|次|条|种|位)?|那俩|这俩|它们|他们|她们")),
    ("event", re.compile(r"这件事|那件事|这回事|那回事|这一次|那一次|这次|那次|那回")),
    ("object", re.compile(r"这个|那个|这些|那些|前面(?:说|提)的|刚才(?:说|提)的|之前那个")),
)

_MAX_MESSAGE_CHARS = 240
_MAX_VARIANT_CHARS = 900


@dataclass(frozen=True)
class RecallQueryEnvelope:
    original_text: str
    variants: tuple[str, ...] = ()
    reference_kind: str = ""
    recent_message_count: int = 0
    recent_user_turn_count: int = 0
    context_text: str = ""

    @property
    def used_recent_context(self) -> bool:
        return bool(self.variants or self.context_text)

    def safe_observation(self) -> dict[str, object]:
        return {
            "used_recent_context": self.used_recent_context,
            "reference_kind": self.reference_kind,
            "variant_count": len(self.variants),
            "recent_message_count": self.recent_message_count,
            "recent_user_turn_count": self.recent_user_turn_count,
        }


def reference_kind(text: str) -> str:
    clean = str(text or "").strip()
    for kind, pattern in _REFERENCE_PATTERNS:
        if pattern.search(clean):
            return kind
    return ""


def build_recall_query_envelope(
    query_text: str,
    recent_messages: Sequence[ConversationMessage] = (),
) -> RecallQueryEnvelope:
    """Keep a bounded recent context without guessing intent from keywords."""

    original = str(query_text or "").strip()
    if not original:
        raise ValueError("recall query text must not be empty")
    kind = reference_kind(original)
    if not recent_messages:
        return RecallQueryEnvelope(original_text=original, reference_kind=kind)

    labels = {"user": "人类伙伴", "assistant": "AI"}
    lines: list[str] = []
    user_turns = 0
    ordinary = [m for m in recent_messages if m.role in labels and m.source_kind == 'chat'][-4:]
    for message in ordinary:
        if message.role not in labels or message.source_kind != "chat":
            continue
        content = sanitize_archive_text(message.content, limit=_MAX_MESSAGE_CHARS)
        if not content:
            continue
        lines.append(f"{labels[message.role]}：{content}")
        if message.role == "user":
            user_turns += 1
    if not lines:
        return RecallQueryEnvelope(original_text=original, reference_kind=kind)

    variant = sanitize_archive_text(
        f"当前问题：{original}\n上文指代线索：\n" + "\n".join(lines),
        limit=_MAX_VARIANT_CHARS,
    )
    return RecallQueryEnvelope(
        original_text=original,
        variants=(variant,) if kind else (),
        reference_kind=kind,
        recent_message_count=len(lines),
        recent_user_turn_count=user_turns,
        context_text=variant,
    )


__all__ = [
    "RecallQueryEnvelope",
    "build_recall_query_envelope",
    "reference_kind",
]
