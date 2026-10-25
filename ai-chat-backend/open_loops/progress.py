"""Source-bound factual progress, independent of AI's attention clock."""
import hashlib
import json
import sqlite3
from pathlib import Path


def source_snapshot(messages, ids, *, context=None):
    if context and context.get('raw_authority_available'):
        return [context['sources'][mid]['raw_snapshot'] for mid in dict.fromkeys(ids)]
    by_id = {m['message_id']: m for m in messages if m.get('message_id')}
    return [{'message_id': mid, 'content_digest': hashlib.sha256(
        str(by_id[mid].get('content') or '').encode('utf-8')).hexdigest()}
        for mid in dict.fromkeys(ids)]


def bind_raw_sources(sources, authority_path):
    from memory_v2.source_reviews import read_raw_rows, raw_revision
    path = Path(authority_path)
    if not path.is_file():
        return False
    rows = read_raw_rows(path, list(sources))
    for mid, row in rows.items():
        if row.get('role') == 'user' and not row.get('event_type'):
            sources[mid]['raw_snapshot'] = {'message_id': mid, 'raw_revision': raw_revision(row)}
    return True


def sources_current(proofs, authority_path):
    probe = {'id': 'probe', 'latest_progress': 'probe', 'fact_sources_json': json.dumps(proofs)}
    return inspect_progress([probe], authority_path)[0]['progress_source_current']


def inspect_progress(items, authority_path):
    """Read current raw sources once; an edited/deleted source is not current fact."""
    from memory_v2.source_reviews import read_raw_rows, raw_revision
    proofs = {}
    for item in items:
        if item.get('latest_progress'):
            try:
                value = json.loads(item.get('fact_sources_json') or '[]')
                proofs[item['id']] = value if isinstance(value, list) and all(isinstance(p, dict) for p in value) else []
            except (ValueError, TypeError):
                proofs[item['id']] = []
    if not proofs:
        return items
    try:
        rows = read_raw_rows(Path(authority_path), [p['message_id'] for ps in proofs.values() for p in ps])
    except (OSError, ValueError, KeyError, sqlite3.Error):
        rows = {}
    for item in items:
        if item['id'] not in proofs:
            continue
        ps = proofs[item['id']]
        item['progress_source_current'] = bool(ps) and all(
            p.get('message_id') in rows and rows[p['message_id']].get('role') == 'user'
            and not rows[p['message_id']].get('event_type')
            and (raw_revision(rows[p['message_id']]) == p['raw_revision'] if p.get('raw_revision') else
                 hashlib.sha256(str(rows[p['message_id']].get('content') or '').encode('utf-8')).hexdigest()
                    == p.get('content_digest')) for p in ps)
    return items


def append_progress_evidence(evidence_index, *, store=None, authority_path=None):
    """Sentinel's decision step also sees current progress and actual quotations."""
    from contextlib import closing
    from .manager import DEFAULT_DB_PATH
    from memory_v2.source_reviews import read_raw_rows
    db_path = store.db_path if store is not None else DEFAULT_DB_PATH
    path = Path(authority_path) if authority_path else db_path.parent / 'event_chronicle.db'
    if store is not None:
        items = store.list_for_context()
    elif not db_path.is_file():
        return ''
    else:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            items = [dict(row) for row in db.execute("SELECT * FROM open_loops WHERE status IN ('active','snoozed')")]
    items = inspect_progress(items, path)
    items = [item for item in items if item.get('status') in {'active', 'snoozed'}
             and item.get('latest_progress') and item.get('progress_source_current')]
    if not items:
        return ''
    ids = [mid for item in items for mid in json.loads(item['fact_source_ids_json'])]
    rows = read_raw_rows(path, ids)
    parts = []
    for item in items:
        proofs = json.loads(item['fact_sources_json'])
        # Recheck before the quoted body is emitted; no stale semantic summary.
        if not sources_current(proofs, path):
            continue
        parts.append(f"[已有挂念的最近进展｜{item['fact_confirmed_at']}]\n{item['latest_progress']}")
        for mid in json.loads(item['fact_source_ids_json']):
            existing = next((key for key, value in evidence_index.items() if value.get('message_id') == mid), None)
            if existing:
                continue
            row = rows[mid]
            key = 'H' + str(len(evidence_index) + 1)
            while key in evidence_index:
                key = 'H' + str(int(key[1:]) + 1)
            evidence_index[key] = {'message_id': mid, 'role': 'user', 'speaker': '人类伙伴',
                                  'timestamp': row['timestamp'], 'content': row['content'],
                                  'content_digest': hashlib.sha256(row['content'].encode('utf8')).hexdigest(),
                                  'source_kind': 'chat'}
            parts.append(f"[{key}｜{row['timestamp']}｜人类伙伴原话] {row['content']}")
    return '\n\n'.join(parts)
