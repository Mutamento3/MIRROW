"""Validated radar quotes enter the existing other-book evidence lane."""
from __future__ import annotations

import logging
import sqlite3

from . import books

logger = logging.getLogger(__name__)


def leads(source_date, session_id, *, source=None, store=None, review_days=5, window_days=30,
          memory_paths=None):
    """An unavailable optional radar must not block direct cognitive evidence."""
    try:
        return _leads(source_date, session_id, source=source, store=store,
                      review_days=review_days, window_days=window_days, memory_paths=memory_paths)
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.warning("Habit evidence unavailable: %s", type(exc).__name__)
        return []


def _leads(source_date, session_id, *, source=None, store=None, review_days=5, window_days=30,
           memory_paths=None):
    """Complete validated statements in the review window, never radar summaries.

This collector currently observes only Human. Other entities retain their
normal attributed interaction and third-party-account evidence lanes.
"""
    from habit_radar.aggregation import material_windows
    from habit_radar.source_lifecycle import filter_current
    from habit_radar.store import ShadowStore
    from memory_v2.conversation_source import ConversationSource
    from memory_v2.day_projection import _configured_paths
    from .habit_frequency_evidence import collect, select
    memory_paths = tuple(memory_paths) if memory_paths is not None else _configured_paths()
    if source is None:
        authority = books.ROOT / "events/event_chronicle.db"
        path = books.ROOT / "events/habit_radar_shadow.db"
        if not authority.is_file() or not path.is_file():
            return []
        source = ConversationSource(authority, canonical_sessions_only=True)
        store = ShadowStore(path)
    if store is None:
        return []
    if review_days < 1 or window_days < 0:
        raise ValueError('invalid_habit_review_window')
    days = sorted((row for row in store.days() if row["active_date"] < source_date),
                  key=lambda row: row["active_date"], reverse=True)
    signals = tuple(signal for row in days
                    for signal in store.signals_for(row["active_date"])
                    if signal.source_session_id == session_id
                    and signal.behavior_subject in {'', 'human'})
    signals, _ = filter_current(signals, source)
    # A recalled occurrence on five dates is not five independent behavior days.
    schedule_signals = tuple(signal for signal in signals if signal.temporal_status in
                             {"occurred", "stated_pattern", "expression"})
    windows = material_windows(schedule_signals)
    result = []
    for dimension, window in windows.items():
        # The window belongs to this dimension's validated material days.
        # Frequent unrelated activity must not evict sparse food/game/etc.
        # sources before they can be compared. These are recording days,
        # never an asserted count of independent real-life occurrences.
        selected_days = set(window.evidence_days[-window_days:] if window_days else window.evidence_days)
        if len(selected_days) < review_days:
            continue
        selected = [signal for signal in schedule_signals if signal.dimension == dimension
                    and signal.source_calendar_date in selected_days]
        if not selected:
            continue
        # Eligibility schedules comparison; it must not hide later corrections,
        # recollections or plans from an already due dimension's full context.
        # Their timestamps do not become new observed behavior days.
        readable = [signal for signal in signals if signal.dimension == dimension
                    and signal.source_calendar_date >= min(selected_days)]
        context_for = {signal.source_message_id: signal.context_source.message_id
                       for signal in readable if signal.context_source}
        rows = source.read_message_ids(tuple(dict.fromkeys(
            [signal.source_message_id for signal in readable] + list(context_for.values())
        )))
        by_id = {row.message_id: row for row in rows}
        # Multiple statements on one day can correct or qualify one another.
        # Do not turn an arbitrary first quote or length cap into the evidence
        # window. Signal count is not the number of actual occurrences.
        selected_ids = {signal.source_message_id for signal in readable}
        schedule_ids = {signal.source_message_id for signal in selected}
        schedule_ids.update(signal.context_source.message_id for signal in selected if signal.context_source)
        evidence, seen = [], set()
        for row in sorted(rows, key=lambda row: (row.timestamp, row.message_id)):
            if row.message_id not in selected_ids or row.role != "user":
                continue
            context = by_id.get(context_for.get(row.message_id))
            for item, speaker in ((context, "k"), (row, "human")):
                if item is None or item.message_id in seen:
                    continue
                evidence.append({"id": item.message_id, "speaker": speaker, "text": item.content,
                                 "timestamp": item.timestamp, "active_date": item.active_date,
                                 "source_kind": "private_chat"})
                seen.add(item.message_id)
        schedule_evidence = [row for row in evidence if row['id'] in schedule_ids]
        if len({row["active_date"] for row in schedule_evidence}) < review_days:
            continue
        # The clue's date is the latest actual source day, not today's analysis
        # run. Sparse historical evidence must not masquerade as a new change.
        last_source_date = max(row['active_date'] for row in schedule_evidence)
        result.append({"subject_id": "human", "entity_name": "人类伙伴", "source_date": last_source_date,
                       "text": f"{dimension} 有跨日原话可复核；未判断习惯成立或实际发生次数。",
                       "reference_ids": [row["id"] for row in evidence],
                       "lead_origin": "habit_radar", "lead_dimension": dimension,
                       "schedule_evidence_ids": [row['id'] for row in schedule_evidence],
                       "evidence": evidence,
                       "frequency_evidence": select(collect(selected, source, memory_paths), evidence)})
    return result
