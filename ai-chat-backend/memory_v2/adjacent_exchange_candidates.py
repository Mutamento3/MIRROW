"""Bounded structural nominations, never a semantic continuity decision."""
from .conversation_source import is_canonical_session_id
from .source_time import instant

MAX_ADJACENT_CANDIDATES = 8
MAX_GAP_SECONDS = 30 * 60


def private_span(event):
    # Tool receipts stay in the model evidence. Only actual private utterances
    # may anchor a turn handoff; a tool-only event cannot nominate one.
    refs = [r for r in event.sources if r.source_role in {'user', 'assistant'}
            and r.source_kind == 'chat' and not r.source_event_type]
    if (not refs or len({r.session_id for r in event.sources}) != 1
            or not is_canonical_session_id(refs[0].session_id)):
        return None
    ordered = sorted(refs, key=lambda r: (instant(r.source_ts), r.message_row_id))
    return ordered[0], ordered[-1]


def nearest_private_predecessor(earlier_events, target):
    """Nominate the nearest AI -> Human exchange from source metadata only."""
    span = private_span(target)
    if span is None or span[0].source_role != 'user':
        return None
    first = span[0]
    choices = []
    for earlier in earlier_events:
        prior = private_span(earlier)
        if prior is None or prior[1].source_role != 'assistant':
            continue
        last = prior[1]
        if last.session_id != first.session_id:
            continue
        if (set(r.message_id for r in earlier.sources) & set(r.message_id for r in target.sources)
                or earlier.source_scope_ids and target.source_scope_ids
                and not set(earlier.source_scope_ids).intersection(target.source_scope_ids)):
            continue
        gap = (instant(first.source_ts) - instant(last.source_ts)).total_seconds()
        if 0 < gap <= MAX_GAP_SECONDS:
            choices.append((instant(last.source_ts), earlier.ref, earlier))
    return max(choices, key=lambda row: row[:2])[2] if choices else None
