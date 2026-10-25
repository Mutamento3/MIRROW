"""Fail-closed read-side validity check for source-backed shadow signals.

No mutation hook is installed: this check is applied when using observations.
An edited, deleted, reclassified, or moved source cannot support an old signal.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Protocol, Sequence

from memory_v2.conversation_source import ConversationMessage

from .extraction import Signal
from .day_material import MAX_CONTEXT_CHARS


class MessageLookup(Protocol):
    def read_message_ids(self, message_ids: Sequence[str]) -> list[ConversationMessage]: ...


def _validated_sources(signals: Sequence[Signal], source: MessageLookup,
                       *, chunk_size: int = 400):
    """Retain only signals with the exact current canonical human source."""
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    ids = tuple(dict.fromkeys(message_id for signal in signals
                             for message_id in (signal.source_message_id,
                                                signal.context_source.message_id if signal.context_source else "")
                             if message_id))
    current: dict[str, ConversationMessage] = {}
    for start in range(0, len(ids), chunk_size):
        current.update({row.message_id: row
                        for row in source.read_message_ids(ids[start:start + chunk_size])})
    valid: list[Signal] = []
    rejected: Counter[str] = Counter()
    for signal in signals:
        row = current.get(signal.source_message_id)
        if row is None:
            rejected["source_missing"] += 1
        elif (not signal.source_session_id or not signal.source_active_date
              or row.session_id != signal.source_session_id
              or row.active_date != signal.source_active_date
              or row.calendar_date != signal.source_calendar_date
              or row.timestamp != signal.source_timestamp
              or row.role != "user" or row.source_kind != "chat" or row.event_type):
            rejected["source_identity_changed"] += 1
        elif hashlib.sha256(row.content.strip().encode("utf-8")).hexdigest() != signal.source_content_digest:
            rejected["source_edited"] += 1
        elif signal.quote not in row.content.strip():
            rejected["quote_missing"] += 1
        else:
            dependency = signal.context_source
            if dependency is not None:
                context = current.get(dependency.message_id)
                if context is None:
                    rejected["context_missing"] += 1
                    continue
                if (context.role != "assistant" or context.source_kind != "chat" or context.event_type
                        or context.session_id != signal.source_session_id
                        or context.active_date != signal.source_active_date
                        or context.timestamp != dependency.timestamp
                        or hashlib.sha256(context.content.strip().encode("utf-8")).hexdigest()
                        != dependency.content_digest):
                    rejected["context_changed"] += 1
                    continue
            valid.append(signal)
    return tuple(valid), dict(sorted(rejected.items())), current


def filter_current(signals: Sequence[Signal], source: MessageLookup,
                   *, chunk_size: int = 400) -> tuple[tuple[Signal, ...], dict[str, int]]:
    current, reasons, _ = _validated_sources(signals, source, chunk_size=chunk_size)
    return current, reasons


def review_material(signals: Sequence[Signal], source: MessageLookup) -> tuple[list[dict], dict[str, int]]:
    """Build in-memory review input from the same validated source snapshot.

An excerpt is an index, not a standalone fact. Always include its full human
message so negations/conditions outside a selected quote remain available.
"""
    valid, reasons, rows = _validated_sources(signals, source)
    material = []
    for index, signal in enumerate(valid, 1):
        human = rows[signal.source_message_id]
        context = rows[signal.context_source.message_id] if signal.context_source else None
        context_text = context.content.strip() if context else ""
        material.append({"n": index, "dimension": signal.dimension, "kind": signal.kind,
                         "temporal_status": signal.temporal_status,
                         "time": human.timestamp, "human": human.content.strip(), "quote": signal.quote,
                         "preceding_k": context_text[-MAX_CONTEXT_CHARS:],
                         "preceding_k_time": context.timestamp if context else "",
                         "preceding_k_truncated": len(context_text) > MAX_CONTEXT_CHARS})
    return material, reasons
