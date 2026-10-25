"""Verify later lane completion against the failed day's current sources.

Old root failures remain immutable. A successful child receipt only supersedes
the displayed failure when its session, complete evidence coverage and durable
item outcomes can be verified. Cached summaries contain hashes, never bodies.
"""
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from . import books, maintenance, world_evolution
from .maintenance_sources import sources

SUCCESS = {'completed', 'completed_with_rejections'}
OTHER_TERMINAL = {'auto_applied', 'applied', 'unchanged', 'rejected', 'superseded'}
WORLD_TERMINAL = OTHER_TERMINAL | {'observing'}
_lock = threading.RLock()
_cache = {}


def source_signature(row):
    return maintenance.digest({k: row.get(k, '') for k in
                               ('id', 'text', 'speaker', 'timestamp', 'source_kind')})


def evidence_for_day(lane, date, session, rows):
    return sources(rows)


def _summary(path):
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        cached = _cache.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        raw = path.read_bytes()
        d = json.loads(raw)
        summary = {'id': path.stem, 'date': d.get('source_date'), 'session': d.get('session_id'),
                   'status': d.get('status'), 'time': stat.st_mtime,
                   'digest': hashlib.sha256(raw).hexdigest(), 'items': d.get('items', []),
                   'completed_batches': d.get('completed_batches'),
                   'coverage': {str(e['id']): source_signature(e)
                                for e in d.get('analysis', {}).get('evidence', [])}}
        _cache[str(path)] = (stamp, summary)
        return summary


def successful_receipts(root_dir, lane, date, session, after=0):
    folder = Path(root_dir) / 'data/cognition_maintenance' / ('other_runs' if lane == 'other' else 'runs')
    return [d for p in folder.glob('*.json') if
            (d := _summary(p))['date'] == date and d['status'] in SUCCESS
            and (d['session'] == session or lane == 'world') and d['time'] >= after]


def _failure_time(root):
    value = root.get('completed_at') or root.get('started_at')
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt.astimezone().timestamp()


def _resumed_other_receipts(root_dir, date, session, evidence, after):
    """An exact completed day receipt can own older, already committed batches."""
    from .other_batches import partition
    batches = partition(evidence)
    if len(batches) <= 1:
        return [], None
    rid = maintenance.digest({'pipeline': 'other-day-batches-v1', 'date': date,
                              'session': session, 'batches': batches})
    folder = Path(root_dir) / 'data/cognition_maintenance'
    path = folder / 'other_day_runs' / (rid + '.json')
    if not path.is_file():
        return [], None
    parent = _summary(path)
    if (parent['status'] not in SUCCESS or parent['time'] < after
            or parent['date'] != date or parent['session'] != session
            or parent['completed_batches'] != len(batches)):
        return [], None
    children = []
    for index, batch in enumerate(batches):
        material = (batches[index - 1][-1:] if index else []) + batch
        child_id = maintenance.digest({'pipeline': 'other-daily-v1', 'date': date,
                                       'session': session, 'evidence': material})
        path = folder / 'other_runs' / (child_id + '.json')
        if not path.is_file():
            return [], None
        child = _summary(path)
        if child['status'] not in SUCCESS or child['date'] != date or child['session'] != session:
            return [], None
        children.append(child)
    if set(parent['items']) != {iid for child in children for iid in child['items']}:
        return [], None
    return children, parent


def find_completion(root, lane, root_dir):
    if lane not in {'other', 'world'}:
        return None
    after = _failure_time(root)
    if after is None:
        return None
    from event_chronicle import get_global_chronicle
    date = root['source_date']
    all_rows = get_global_chronicle().get_messages_by_date(date)
    session = root.get('session_id')
    if not session:
        # Legacy roots lack session_id. Infer only from an unambiguous authority,
        # never choose the largest session or a same-date child arbitrarily.
        sessions = {r['session_id'] for r in all_rows}
        if len(sessions) != 1:
            return None
        session = next(iter(sessions))
    rows = [r for r in all_rows if r['session_id'] == session]
    evidence = evidence_for_day(lane, date, session, rows)
    if not evidence:
        return None
    needed = {str(e['id']): source_signature(e) for e in evidence}
    receipts = successful_receipts(root_dir, lane, date, session, after)
    if lane == 'world':
        expected = maintenance.digest({'date': date, 'session': session, 'messages': evidence,
                                       'pipeline': world_evolution.PIPELINE})
        receipts = [r for r in receipts if r['id'] == expected]
        if not receipts:
            return None
    else:
        reused, parent = _resumed_other_receipts(root_dir, date, session, evidence, after)
        if reused:
            receipts = reused
        receipts = [r for r in receipts if any(needed.get(i) == sig for i, sig in r['coverage'].items())]
        covered = {i for r in receipts for i, sig in r['coverage'].items() if needed.get(i) == sig}
        if covered != set(needed):
            return None
        if parent:
            receipts.append(parent)
    outcomes = {}
    folder = Path(root_dir) / 'data/cognition_maintenance/items'
    for iid in {iid for r in receipts for iid in r['items']}:
        if len(iid) != 64 or any(c not in '0123456789abcdef' for c in iid):
            return None
        path = folder / (iid + '.json')
        if not path.is_file():
            return None
        item = json.loads(path.read_text('utf8'))
        status = item.get('status')
        isolated = lane == 'world' and status == 'isolated'
        if isolated:
            # Native evolution deliberately retains invalid candidates. This is
            # a processed outcome only for its exact, source-covered parent run;
            # it is never proof that the proposed fact reached a book.
            owner = next((r for r in receipts if r['id'] == item.get('run_id') and iid in r['items']), None)
            if (not owner or owner['status'] != 'completed_with_rejections'
                    or item.get('evolution_version') != 2
                    or not set(needed).issubset(set(item.get('input_source_ids', [])))):
                return None
        elif status not in (OTHER_TERMINAL if lane == 'other' else WORLD_TERMINAL):
            return None
        if item.get('source_date') != date or item.get('proposal', {}).get('domain') != lane:
            return None
        # Native item receipts bind to the session through their parent run;
        # older items themselves have no session_id field.
        if item.get('session_id', session) != session:
            return None
        if status in {'auto_applied', 'applied'}:
            plan = item.get('plan', {})
            if not books.receipt_present(lane, plan.get('name', ''), 'maintenance:' + iid):
                return None
        outcomes[status] = outcomes.get(status, 0) + 1
    return {'source_count': len(needed), 'source_digest': maintenance.digest(needed),
            'receipts': [{'id': r['id'], 'digest': r['digest'], 'status': r['status']} for r in receipts],
            'outcomes': outcomes, 'status': 'completed_with_rejections' if any(
                r['status'] == 'completed_with_rejections' for r in receipts) else 'completed'}
