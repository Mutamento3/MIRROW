"""Closed-day semantic boxes: source windows, not event identities."""
from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256

VERSION = 'scene-reconstruction-v1'


def identity(kind, refs, extra=()):
    body = [VERSION, kind, [asdict(ref) for ref in refs], extra]
    return sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def parse_plan(value, box_count):
    body = json.loads(value) if isinstance(value, str) else value
    if not isinstance(body, dict) or set(body) != {'windows'} or not isinstance(body['windows'], list):
        raise ValueError('scene_plan_contract')
    previous, windows = 0, []
    for item in body['windows']:
        if not isinstance(item, dict) or set(item) != {'end_box', 'review'} or type(item['end_box']) is not int or type(item['review']) is not bool:
            raise ValueError('scene_plan_window_contract')
        end = item['end_box']
        if not previous < end <= box_count or (end - previous > 1 and not item['review']):
            raise ValueError('scene_plan_source_partition')
        windows.append((previous, end, item['review']))
        previous = end
    if previous != box_count:
        raise ValueError('scene_plan_incomplete')
    return windows


def parse_segments(value, refs, *, has_previous=False):
    body = json.loads(value) if isinstance(value, str) else value
    if not isinstance(body, dict) or set(body) != {'continues_previous', 'segments'} or type(body['continues_previous']) is not bool:
        raise ValueError('scene_segment_contract')
    if body['continues_previous'] and not has_previous:
        raise ValueError('scene_segment_missing_previous')
    segments = body['segments']
    if isinstance(segments, list):
        # Some JSON-mode routes repeat the outer container inside the list.
        # Only a false continuation wrapper has an unambiguous structural
        # projection. Preserve all leaf values/order and validate them below;
        # true continuation or invented fields remain contract errors.
        flattened, pending = [], list(reversed(segments))
        while pending:
            item = pending.pop()
            if (isinstance(item, dict) and set(item) == {'continues_previous', 'segments'}
                    and item['continues_previous'] is False
                    and isinstance(item['segments'], list) and item['segments']):
                pending.extend(reversed(item['segments']))
            else:
                flattened.append(item)
        segments = flattened
    if not isinstance(segments, list) or not 1 <= len(segments) <= len(refs):
        raise ValueError('scene_segment_count')
    start, result = 0, []
    for item in segments:
        if not isinstance(item, dict) or set(item) != {'end_source', 'summary'}:
            raise ValueError('scene_segment_fields')
        end, summary = item['end_source'], item['summary']
        if type(end) is not int or not start < end <= len(refs) or not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 6000:
            raise ValueError('scene_segment_source_partition')
        result.append({'summary': summary.strip(), 'message_ids': [r.message_id for r in refs[start:end]],
                       'summary_source_message_ids': [r.message_id for r in refs[start:end]]})
        start = end
    if start != len(refs):
        raise ValueError('scene_segment_incomplete')
    return result, body['continues_previous']


def validate_partition(items, refs):
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('scene_reconstruction_items')
    for item in items:
        if not isinstance(item.get('message_ids'), list) or not isinstance(item.get('summary'), str):
            raise ValueError('scene_reconstruction_item_contract')
        if not isinstance(item.get('summary_source_message_ids', []), list):
            raise ValueError('scene_reconstruction_summary_evidence')
    emitted = [mid for item in items for mid in item['message_ids']]
    if emitted != [ref.message_id for ref in refs] or len(set(emitted)) != len(emitted):
        raise ValueError('scene_reconstruction_source_partition')
    positions = {ref.message_id: ref for ref in refs}
    for item in items:
        if not item['message_ids'] or len({positions[mid].session_id for mid in item['message_ids']}) != 1:
            raise ValueError('scene_reconstruction_session_boundary')
        if not set(item.get('summary_source_message_ids', ())).issubset(item['message_ids']):
            raise ValueError('scene_reconstruction_summary_evidence')


def geometry(boxes, source, day):
    """Includes divider metadata absent from the accepted-message manifest."""
    with source._connect() as db:
        dividers = [tuple(row) for row in db.execute(
            "SELECT id,session_id,timestamp FROM conversation_messages "
            "WHERE active_date=? AND role='divider' ORDER BY timestamp,id", (day,))]
    refs = tuple(ref for box in boxes for batch in box.batches for ref in batch.batch_sources)
    shape = [(box.box_id, box.session_id, box.closed,
              [ref.message_id for batch in box.batches for ref in batch.batch_sources]) for box in boxes]
    return identity('geometry', refs, [shape, dividers])


PLAN_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['windows'],
    'properties': {'windows': {'type': 'array', 'minItems': 1, 'items': {'type': 'object',
        'additionalProperties': False, 'required': ['end_box', 'review'], 'properties': {
            'end_box': {'type': 'integer', 'minimum': 1}, 'review': {'type': 'boolean'}}}}}}
SEGMENT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['continues_previous', 'segments'],
    'properties': {'continues_previous': {'type': 'boolean'}, 'segments': {'type': 'array', 'minItems': 1,
        'items': {'type': 'object', 'additionalProperties': False, 'required': ['end_source', 'summary'],
            'properties': {'end_source': {'type': 'integer', 'minimum': 1},
                           'summary': {'type': 'string', 'minLength': 1, 'maxLength': 6000}}}}}}
