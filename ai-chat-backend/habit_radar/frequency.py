"""Two separate, source-backed frequency counts; uncertainty remains visible."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from hashlib import sha256


@dataclass(frozen=True)
class FrequencyEvidence:
    subject_id: str
    pattern_key: str
    occurrence_key: str
    discussion_key: str
    source_message_id: str
    source_date: str
    temporal_status: str
    source_content_digest: str = ""
    behavior_subject: str = ""
    behavior_type: str = ""
    binding_basis: str = ""
    quote: str = ""


def matching_behavior(signal, event_type, attributes):
    if attributes.get('source_update_report') is True:
        return None
    facets = {a.get('event_type') for a in attributes.get('event_aspects', [])
              if signal.source_message_id in a.get('source_message_ids', [])}
    if signal.behavior_subject:
        if signal.behavior_subject != 'human':
            return None
        if signal.behavior_type == event_type:
            return event_type, 'event'
        if signal.behavior_type in facets:
            return signal.behavior_type, 'facet'
        return None
    if len(facets | {event_type}) > 1:
        return None
    return event_type, 'legacy_event_anchor'


def count_frequencies(evidence):
    grouped = defaultdict(list)
    for item in evidence:
        grouped[item.subject_id, item.pattern_key].append(item)
    result = []
    for (subject, pattern), rows in sorted(grouped.items()):
        occurrences, discussions = set(), defaultdict(set)
        unresolved = set()
        for row in rows:
            if row.temporal_status not in {"occurred", "recollection"}:
                unresolved.add(row.source_message_id)
                continue
            if not row.occurrence_key:
                unresolved.add(row.source_message_id)
                continue
            occurrences.add(row.occurrence_key)
            discussions[row.occurrence_key].add(row.discussion_key)
        dates = sorted({row.source_date for row in rows if row.source_date})
        result.append({"subject_id": subject, "pattern_key": pattern,
                       "recorded_occurrences": len(occurrences),
                       "recorded_discussions": sum(len(values) for values in discussions.values()),
                       "revisited_occurrences": sum(len(values) > 1 for values in discussions.values()),
                       "unresolved_sources": len(unresolved),
                       "source_window_start": dates[0] if dates else "",
                       "source_window_end": dates[-1] if dates else "",
                       "count_scope": "recorded_sources_not_actual_life_totals"})
    return result


def resolve_signals(signals, source, memory_paths):
    """Use existing event/thread anchors only, never invent semantic groups.

Dimension + event type is a coarse diagnostic category, not proof that all
such events are the same habit. A later semantic review may refine patterns.
"""
    from .source_lifecycle import _validated_sources
    from .extraction import DIMENSIONS
    signals, stale, current = _validated_sources(signals, source)
    result = []
    for signal in signals:
        if signal.dimension not in DIMENSIONS or signal.behavior_subject not in {'', 'human'}:
            continue
        matches = defaultdict(set)
        for raw_path in memory_paths:
            path = Path(raw_path).resolve()
            if not path.is_file():
                continue
            try:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    rows = db.execute("""SELECT e.id,e.event_type,e.attributes_json,te.thread_id
                        FROM events e JOIN event_sources s ON s.event_id=e.id
                        JOIN encoding_batch_sources bs ON bs.batch_id=e.batch_id
                          AND bs.message_id=s.message_id
                        LEFT JOIN thread_events te ON te.event_id=e.id AND COALESCE((SELECT ts.status
                         FROM event_thread_status_log ts WHERE ts.thread_id=te.thread_id
                         ORDER BY ts.created_at DESC,ts.id DESC LIMIT 1),'active')='active'
                        WHERE s.message_id=? AND bs.content_digest=? AND e.subject_id='human'
                        AND COALESCE((SELECT st.status FROM event_status_log st WHERE st.event_id=e.id
                          ORDER BY st.created_at DESC,st.id DESC LIMIT 1),'active')='active'""",
                        (signal.source_message_id,
                         current[signal.source_message_id].as_batch_source_ref().content_digest)).fetchall()
                for row in rows:
                    attributes = json.loads(row["attributes_json"] or "{}")
                    behavior = matching_behavior(signal, row['event_type'], attributes)
                    if behavior is None or behavior[0] in {'plan', 'preference', 'request', 'reflection'}:
                        continue
                    if not row["thread_id"] and attributes.get("temporal_basis") == "unresolved":
                        continue
                    if signal.temporal_status == "recollection" and not row["thread_id"]:
                        continue
                    namespace = sha256(str(path).encode('utf8')).hexdigest()[:16]
                    identity = ('thread:' + row['thread_id'] if row['thread_id']
                                else 'event:' + row['id'])
                    matches[namespace, identity, behavior[0]].add(behavior[1])
            except (sqlite3.Error, ValueError, OSError):
                continue
        anchor = next(iter(matches)) if len(matches) == 1 else None
        # Without an exact scene identity, count one discussion per source day
        # conservatively. Never count each short reply as a new discussion.
        discussion = signal.source_active_date or signal.source_calendar_date
        try:
            with source._connect() as db:
                rows = db.execute("SELECT id FROM conversation_scenes WHERE active_date=? AND session_id=? "
                                  "AND julianday(start_ts)<=julianday(?) AND julianday(end_ts)>=julianday(?)",
                                  (signal.source_active_date, signal.source_session_id,
                                   signal.source_timestamp, signal.source_timestamp)).fetchall()
            if len(rows) == 1:
                discussion = rows[0]["id"]
        except (AttributeError, sqlite3.Error):
            pass
        result.append(FrequencyEvidence(
            "human", signal.dimension + ":" + (anchor[2] if anchor else "unresolved"),
            ":".join(anchor[:2]) if anchor else "", str(discussion), signal.source_message_id,
            signal.source_calendar_date, signal.temporal_status,
            signal.source_content_digest, signal.behavior_subject, signal.behavior_type,
            '+'.join(sorted(matches[anchor])) if anchor else 'unresolved', signal.quote,
        ))
    return tuple(result), stale


def project_signals(signals, source, memory_paths):
    """Body-free diagnostics over the same source-bound identities used in review."""
    result, stale = resolve_signals(signals, source, memory_paths)
    return {"groups": count_frequencies(result), "stale_reasons": stale,
            "semantics": "coarse_event_categories_not_verified_habit_patterns",
            "discussion_unit": "scene_when_available_otherwise_source_day"}
