"""No-progress warnings are advisory, never execution deadlines."""
from datetime import datetime

UNBOUNDED_TASKS = frozenset({'memory_v2_online_ingest', 'unified_analysis'})


def check_stall(name, state, now, default_seconds=300):
    """Update the warning; return elapsed seconds only on a new warning."""
    if name in UNBOUNDED_TASKS or state.get('status') != 'running':
        state.pop('stalled', None)
        return None
    waiting = (state.get('routing_wait') or {}).get('retry_after')
    if waiting:
        from time_utils import parse_ts
        current = now if now.tzinfo else now.replace(tzinfo=parse_ts(waiting).tzinfo)
        if current < parse_ts(waiting):
            state.pop('stalled', None)
            return None
    stamp = state.get('last_progress') or state.get('started_at')
    try:
        elapsed = (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None
    if elapsed <= default_seconds:
        state.pop('stalled', None)
    elif not state.get('stalled'):
        state['stalled'] = True
        return elapsed
    return None
