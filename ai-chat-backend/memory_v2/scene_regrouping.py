"""Source-checked maintenance of an over-fragmented completed-day cache.

Only adjacent cached segments may merge. Original sources and canonical events
remain unchanged; uncertain details can still be checked through those sources.
"""
from __future__ import annotations

import json

from time_utils import parse_ts
from .scene_digest_store import SceneDigestStore, cache_path
from .scene_planner import SceneReplayPlanner, scene_start_date
from .scene_reconstruction import _locks, _provider
from .scene_reconstruction_contract import geometry, identity, validate_partition
from .scene_reconstruction_store import ReconstructionStore
from .scene_summary_contract import SUMMARY_VERSION, version_for_date
from .scene_regrouping_prompt import regroup_messages, summary_character_budget

REGROUP_CONTRACT_VERSION = 'cache-regroup-thinking-v1'
REGROUP_OUTPUT_TOKENS = 32768  # Full JSON allowance; provider schema remains active.


def needs_regroup(parts, *, active_date=''):
    """Structure only selects a review; the model owns semantic boundaries."""
    if not parts or all(p.get('regroup_version') == REGROUP_CONTRACT_VERSION for p in parts):
        return False
    if any(p.get('summary_version') != version_for_date(active_date) for p in parts):
        return True
    small = [len(p['message_ids']) <= 3 for p in parts]
    run = longest = 0
    for flag in small:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    fragmented = len(parts) >= 12 and sum(small) * 3 >= len(parts) and longest >= 3
    return fragmented or sum(len(str(p.get('summary') or '')) for p in parts) > 7500

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['segments'],
    'properties': {'segments': {'type': 'array', 'minItems': 1, 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['end_segment', 'summary'],
        'properties': {'end_segment': {'type': 'integer', 'minimum': 1},
                       'summary': {'type': 'string', 'minLength': 1, 'maxLength': 6000}}}}}}


def _parse(value, old, blocks, refs, *, summary_version=SUMMARY_VERSION):
    if not isinstance(value, dict) or not isinstance(value.get('segments'), list):
        raise ValueError('scene_regroup_contract')
    start, result = 0, []
    for item in value['segments']:
        # Only the declared endpoint/summary projection can enter the view.
        # Extra model commentary has no authority and is never retained there.
        if not isinstance(item, dict) or not {'end_segment', 'summary'}.issubset(item):
            raise ValueError('scene_regroup_fields')
        end, summary = item['end_segment'], item['summary']
        if type(end) is not int or not start < end <= len(old) or not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 6000:
            raise ValueError('scene_regroup_partition')
        if len(set(blocks[start:end])) != 1:
            raise ValueError('scene_regroup_explicit_boundary')
        members = old[start:end]
        result.append({'summary': summary.strip(),
            'message_ids': [mid for part in members for mid in part['message_ids']],
            'summary_source_message_ids': list(dict.fromkeys(mid for part in members
                for mid in part.get('summary_source_message_ids', ()))),
            'communication_summaries': [communication for part in members
                for communication in part.get('communication_summaries', ())],
            'parent_box_ids': list(dict.fromkeys(mid for part in members for mid in part.get('parent_box_ids', ()))),
            'vividness': max((part.get('vividness', 0.0) for part in members), default=0.0),
            'summary_version': summary_version,
            'regroup_version': REGROUP_CONTRACT_VERSION})
        start = end
    if start != len(old):
        raise ValueError('scene_regroup_incomplete')
    validate_partition(result, refs)
    return result


async def regroup_cached_day(source, day, *, completed_before, cache=None, model_call=None, explicit_retry=False):
    """Explicit, source-checked cache replacement; caller owns backup/approval."""
    import asyncio
    report = {'status': 'skipped', 'request_count': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
    enabled = scene_start_date()
    if not enabled or day < enabled or not completed_before or day >= completed_before:
        return report
    async with _locks.setdefault((str(source.db_path), day), asyncio.Lock()):
        try:
            return await _regroup(source, day, completed_before, cache, model_call or _provider, report, explicit_retry=explicit_retry)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            usage = getattr(exc, 'usage', {})
            report['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
            report['completion_tokens'] += int(usage.get('completion_tokens') or 0)
            from .scene_failure_diagnostics import failure_report
            return failure_report(report, exc)


async def _regroup(source, day, cutoff, cache, call, report, max_requests=None, *, explicit_retry=False):
    from scene_manager.scene import configured_gap_minutes
    planner = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes())
    boxes = planner.boxes_for_day(day, completed_before=cutoff)
    if not boxes or not all(box.closed for box in boxes):
        raise ValueError('scene_regroup_open_day')
    refs = tuple(ref for box in boxes for batch in box.batches for ref in batch.batch_sources)
    snapshot = geometry(boxes, source, day)
    cache = cache or SceneDigestStore(cache_path(source))
    receipt = ReconstructionStore(cache.path)
    old = receipt.load(day, refs, source, geometry=snapshot)
    if old is None:
        return {**report, 'status': 'pending_reconstruction'}
    report.update(before_segments=len(old), source_count=len(refs))
    if not needs_regroup(old, active_date=day):
        return {**report, 'status': 'cached', 'segment_count': len(old),
                'granularity_outcome': old[0].get('regroup_outcome', 'not_required')}
    by_id = {ref.message_id: ref for ref in refs}
    with source._connect() as db:
        dividers = [(row['session_id'], parse_ts(row['timestamp']), row['id']) for row in db.execute(
            "SELECT id,session_id,timestamp FROM conversation_messages WHERE active_date=? AND role='divider'", (day,))]
    def block(ref):
        return (ref.session_id, sum(session == ref.session_id and (clock, row_id) <
            (parse_ts(ref.source_ts), ref.message_row_id) for session, clock, row_id in dividers))
    blocks, metadata = [], []
    for number, part in enumerate(old, 1):
        selected = [by_id[mid] for mid in part['message_ids']]
        hard_blocks = {block(ref) for ref in selected}
        if len(hard_blocks) != 1:
            raise ValueError('scene_regroup_existing_boundary')
        blocks.append(block(selected[0]))
        metadata.append({'segment': number, 'summary': part['summary'], 'source_count': len(selected),
                         'start': selected[0].source_ts, 'end': selected[-1].source_ts,
                         'boundary_block': blocks.index(blocks[-1]) + 1})
    summary_version = version_for_date(day)
    key = identity('regroup', refs, [summary_version, REGROUP_CONTRACT_VERSION, snapshot, old])
    from .scene_regrouping_output import validated_output
    result = await validated_output(receipt=receipt, key=key, refs=refs, source=source,
        messages=regroup_messages(metadata, active_date=day), call=call, schema=SCHEMA, max_tokens=REGROUP_OUTPUT_TOKENS,
        report=report, validate=lambda value: _parse(value, old, blocks, refs, summary_version=summary_version),
        explicit_retry=explicit_retry, max_requests=max_requests)
    if result is None:
        return {**report, 'status': 'pending', 'pending_granularity_review': True}
    outcome = 'coarsened' if len(result) < len(old) else 'unchanged'
    latest = planner.boxes_for_day(day, completed_before=cutoff)
    current_refs = tuple(ref for box in latest for batch in box.batches for ref in batch.batch_sources)
    if current_refs != refs or geometry(latest, source, day) != snapshot or not all(box.closed for box in latest):
        raise ValueError('scene_reconstruction_source_changed')
    budget = summary_character_budget(old)
    old_chars = sum(len(p['summary']) for p in old)
    summary_chars = sum(len(p['summary']) for p in result)
    budget_met = summary_chars <= budget
    prose_outcome = 'compressed' if old_chars > budget and summary_chars < old_chars else 'reviewed'
    if outcome == 'unchanged' and prose_outcome != 'compressed':
        # A valid JSON rewrite is not evidence of improved granularity. Keep
        # the old prose rather than accumulating unneeded reinterpretations.
        # The review receipt still prevents repeated paid calls for this input.
        result = [{**part, 'regroup_version': REGROUP_CONTRACT_VERSION,
                   'regroup_outcome': outcome} for part in old]
    else:
        for part in result:
            part['regroup_outcome'] = outcome
    for part in result:
        part['prose_outcome'] = prose_outcome
        part['summary_budget_met'] = budget_met
    receipt.save(day, refs, result, source, geometry=snapshot)
    return {**report, 'status': 'complete', 'segment_count': len(result),
            'granularity_outcome': outcome, 'prose_outcome': prose_outcome,
            'summary_characters': summary_chars, 'summary_character_target': budget,
            'summary_budget_met': budget_met}
