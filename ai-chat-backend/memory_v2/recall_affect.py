"""Bind rendered event nodes to authored Affect sources, as a read projection."""
from dataclasses import replace
import json
from pathlib import Path

from affect.memory_echo import MemoryFeelingSource, read_memory_echo
from .source_reviews import read_raw_rows
from .source_validation import nodes_and_proofs, validate_sources


def append_recall_echo(service, projection, *, affect_path=None, now=""):
    """Called only at enabled automatic/active injection boundaries, never shadow."""
    empty = {"status": "no_rendered_event", "count": 0, "chars": 0}
    event_ids = set(projection.rendered_event_ids)
    if projection.echo_event_ids is not None:
        event_ids.intersection_update(projection.echo_event_ids)
    if not projection.text or not event_ids:
        return projection, empty
    try:
        sources = []
        for doc in projection.rendered_event_documents:
            selected = event_ids.intersection(doc.event_ids)
            if doc.source_layer != 'event' or not selected:
                continue
            # Recheck canonical spans and independently versioned human reviews
            # at this read boundary; cached query results are not source proof.
            nodes, proofs = nodes_and_proofs(service, doc)
            validate_sources(service, doc, proofs)
            dates = {n['id']: n['active_date'] for n in nodes}
            raw = read_raw_rows(service.source.db_path, doc.source_message_ids)
            for proof in proofs:
                eid, mid = proof['event_id'], proof['message_id']
                if eid not in selected or mid not in raw:
                    continue
                row = raw[mid]
                if row['role'] == 'assistant':
                    sources.append(MemoryFeelingSource(eid, dates[eid], 'conversation_message', mid, row['content']))
                if row['role'] == 'event' and row.get('event_type') == 'wander_activity':
                    for call in json.loads(row.get('tool_calls') or '[]'):
                        extra = call.get('extra_data') or {}
                        overview = extra.get('execution_overview') or {}
                        activity_id = overview.get('activity_id')
                        if activity_id:
                            sources.append(MemoryFeelingSource(eid, dates[eid], 'wander_settlement', str(activity_id)))
        path = affect_path or Path(service.source.db_path).resolve().parent / 'affect.db'
        remaining = max(0, projection.hard_char_limit - len(projection.text) - 2)
        echo = read_memory_echo(path, sources, now=now, max_chars=min(480, remaining))
        if not echo.text:
            return projection, echo.safe_observation()
        return replace(projection, text=projection.text + '\n\n' + echo.text), echo.safe_observation()
    except Exception as exc:
        # Optional body-state material cannot take episodic recall offline.
        return projection, {"status": "unavailable", "count": 0, "chars": 0,
                            "error_type": type(exc).__name__}
