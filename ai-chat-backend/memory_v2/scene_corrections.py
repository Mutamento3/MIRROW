"""Reconcile rebuilt scene impressions with explicit event corrections.

Source identity alone cannot validate a summary after a semantic correction.
Only affected windows fall back to their exact original messages; no model call
or guessed replacement of a subphrase is performed on this read path.
"""
from contextlib import closing
from pathlib import Path
import sqlite3


def corrected_source_ids(day, memory_paths=None, *, authority_path=None):
    from .day_projection import _configured_paths
    ids = set()
    for path in (_configured_paths() if memory_paths is None else memory_paths):
        path = Path(path).resolve()
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            # Latest record status, rather than every historical supersession.
            rows = db.execute('''SELECT src.message_id FROM events e
                JOIN event_sources src ON src.event_id=e.id
                JOIN event_status_log s ON s.id=(SELECT id FROM event_status_log
                  WHERE event_id=e.id ORDER BY created_at DESC,id DESC LIMIT 1)
                WHERE e.active_date=? AND s.status IN ('superseded','retracted')''', (day,))
            ids.update(row[0] for row in rows)
            if authority_path is not None:
                from .source_reviews import EventSourceReviews
                events = [dict(r) for r in db.execute('SELECT * FROM events WHERE active_date=?', (day,))]
                sources = {e['id']: [dict(r) for r in db.execute('SELECT * FROM event_sources WHERE event_id=?', (e['id'],))]
                           for e in events}
                reviewed = EventSourceReviews(authority_path).prepare(events, sources)
                for event in events:
                    view = reviewed.get(event['id'])
                    if view and view['summary'] != event['summary']:
                        ids.update(r['message_id'] for r in sources[event['id']])
    return ids


def reconcile_boxes(boxes, corrected_ids):
    for box in boxes:
        for segment in box['segments']:
            if corrected_ids.intersection(ref['message_id'] for ref in segment['source_refs']):
                segment.update(kind='raw', summary='', communication_summaries=[],
                               summary_source_message_ids=[], raw_reason='event_semantics_corrected')
                box['digest_ready'] = False
    return boxes


def corrected_cross_day_projection(material, compact):
    """Current factual compact plus unaffected scene/communication windows.

An old overlapping scene cannot replace the current compact. The existing
cross-day reader still owns the raw excerpts and their exact coverage ledger.
"""
    if not compact or not any(s.get('raw_reason') == 'event_semantics_corrected'
                             for b in material['boxes'] for s in b['segments']):
        return None
    from .scene_material import cross_day_projection
    segments = [s for b in material['boxes'] for s in b['segments']]
    changed = {r['message_id'] for s in segments if s.get('raw_reason') == 'event_semantics_corrected'
               for r in s['source_refs']}
    # A selective compact replaces only corrected windows, not the whole day.
    items = [item for item in compact['items'] if changed.intersection(item['message_ids'])]
    covered = {mid for item in items for mid in item['message_ids']}
    for box in material['boxes']:
        for segment in box['segments']:
            refs = [r for r in segment['source_refs'] if r['message_id'] not in covered]
            if not refs:
                continue
            if segment['kind'] == 'summary' and len(refs) == len(segment['source_refs']):
                projected = cross_day_projection({**material, 'boxes': [{**box, 'segments': [segment]}]})
                if projected:
                    items.extend(projected['items'])
                    continue
            # Partial overlap is not permission to discard the remaining sources
            # or retain a contradictory full summary. Keep their original words.
            items.append({'summary': '这一段仍保留原话，尚无可用梗概。', 'raw_required': True,
                          'message_ids': [r['message_id'] for r in refs],
                          'start_ts': refs[0]['source_ts'], 'end_ts': refs[-1]['source_ts'],
                          'source_count': len(refs), 'vividness': segment.get('vividness', 0)})
    items.sort(key=lambda item: (item.get('start_ts', ''), item.get('order', 0)))
    return {**compact, 'items': [{**item, 'order': i} for i, item in enumerate(items)]}
