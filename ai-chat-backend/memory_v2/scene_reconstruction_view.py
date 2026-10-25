"""Read-only semantic-box projection; raw time indexes remain unchanged."""
from dataclasses import asdict

from .day_projection import render_k_facing_summary
from .models import digest_batch_sources
from .scene_reconstruction_contract import geometry, identity
from .scene_reconstruction_store import ReconstructionStore


def reconstructed_boxes(source, day, boxes, cache, *, completed_before, session_id=None):
    if not boxes or not all(box.closed for box in boxes):
        return None
    refs = tuple(ref for box in boxes for batch in box.batches for ref in batch.batch_sources)
    snapshot = geometry(boxes, source, day)
    result = ReconstructionStore(cache.path).load(day, refs, source, geometry=snapshot)
    if result is None:
        return None
    by_id = {ref.message_id: ref for ref in refs}
    rows = {row.message_id: row for row in source.read_message_ids(tuple(by_id))}
    output = []
    for item in result:
        selected = tuple(by_id[mid] for mid in item['message_ids'])
        if session_id is not None and selected[0].session_id != session_id:
            continue
        parents = item.get('parent_box_ids', [])
        segment_id = 'segment_' + identity('view', selected, item)[:24]
        summary = render_k_facing_summary(item['summary'])
        segment = {
            'segment_id': segment_id, 'parent_box_id': parents[0] if parents else '',
            'parent_box_ids': parents, 'stage': 'settled',
            'kind': 'summary' if summary or item.get('communication_summaries') else 'raw',
            'raw_reason': '' if summary or item.get('communication_summaries') else 'no_event_summary', 'summary': summary,
            'source_digest': digest_batch_sources(selected), 'source_count': len(selected),
            'summary_source_message_ids': item.get('summary_source_message_ids', []),
            'communication_summaries': item.get('communication_summaries', []),
            'vividness': item.get('vividness', 0.0), 'start_ts': selected[0].source_ts,
            'end_ts': selected[-1].source_ts, 'source_refs': [asdict(ref) for ref in selected],
            'messages': [{'message_id': ref.message_id, 'timestamp': rows[ref.message_id].timestamp,
                          'role': rows[ref.message_id].role, 'source_kind': rows[ref.message_id].source_kind,
                          'content': rows[ref.message_id].content} for ref in selected]}
        output.append({'box_id': 'semantic_' + segment_id[8:], 'session_id': selected[0].session_id,
                       'parent_box_ids': parents, 'state': 'closed', 'digest_ready': True,
                       'source_count': len(selected), 'source_digest': segment['source_digest'],
                       'segments': [segment]})
    # Verify growth, divider changes and edits, not only rows already selected.
    from .scene_planner import SceneReplayPlanner
    from scene_manager.scene import configured_gap_minutes
    latest = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes()).boxes_for_day(
        day, completed_before=completed_before)
    if geometry(latest, source, day) != snapshot or not source.validate_batch_sources(refs):
        raise ValueError('scene_material_source_changed')
    return output
