"""Complete existing time windows for new habit requests, never counts."""
import hashlib
import json

VERSION = 'habit-chat-windows-v1'


def eligible(row, session_id, source_date):
    return (row.session_id == session_id and row.role in {'user', 'assistant'}
            and row.source_kind == 'chat' and not row.event_type and row.active_date
            and row.active_date < source_date and bool(row.content.strip()))


def material(row):
    return {'id': row.message_id, 'speaker': 'human' if row.role == 'user' else 'k',
            'text': row.content, 'timestamp': row.timestamp, 'active_date': row.active_date,
            'source_kind': 'private_chat', 'identity_basis': 'validated_canonical_chat'}


def collect(source, anchor_ids, session_id, source_date, *, gap_minutes=None):
    from memory_v2.scene_planner import SceneReplayPlanner
    from scene_manager.scene import configured_gap_minutes
    gap = configured_gap_minutes() if gap_minutes is None else gap_minutes
    planner = SceneReplayPlanner(source, gap_minutes=gap)
    anchors = [r for r in source.read_message_ids(tuple(dict.fromkeys(anchor_ids)))
               if eligible(r, session_id, source_date)]
    rows, windows = {}, []
    ids = {r.message_id for r in anchors}
    for day in sorted({r.active_date for r in anchors}):
        for box in planner.boxes_for_day(day, completed_before=source_date):
            members = [r.message_id for batch in box.batches for r in batch.batch_sources]
            if box.session_id != session_id or not ids.intersection(members):
                continue
            window = [r for r in source.read_message_ids(tuple(members))
                      if eligible(r, session_id, source_date)]
            windows.append({'box_id': box.box_id, 'active_date': day,
                'anchor_ids': sorted(ids.intersection(members)),
                'source_ids': [r.message_id for r in window]})
            for row in window:
                rows[row.message_id] = material(row)
    result = sorted(rows.values(), key=lambda r: (r['timestamp'], r['id']))
    proof = hashlib.sha256(json.dumps({'rows': result, 'windows': windows},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result, {'version': VERSION, 'gap_minutes': gap,
        'anchor_ids': list(dict.fromkeys(anchor_ids)), 'windows': windows,
        'identity_proof': proof, 'source_count': len(result),
        'scope': 'complete_time_windows_not_semantic_topics_or_occurrence_counts'}
