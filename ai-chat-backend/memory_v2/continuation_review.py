"""Persisted independent occurrence review, before any proposed edge is committed."""
import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from .continuation_evidence import SourceBoundContinuationModel, manifest
from .day_settlement_checkpoint import _digest, atomic_write
from .day_settlement import parse_continuation_judgments, day_settlement_response_schema

ROUTE = 'memory_continuation_review'
VERSION = 'occurrence-review-v2'


class ContinuationReviewError(RuntimeError):
    pass


async def review(store, plan, judgments, *, source, provider=None, allow_call=True, usage=None, max_calls=1):
    proposed = {j.candidate_ref for j in judgments if j.outcome == 'continue'}
    if not proposed:
        return judgments
    from .continuation_pages import page_messages, PAGE_PROMPT_VERSION
    # Keep competitors and complete thread context for identity comparison, but
    # do not expose the initial model's outcome, confidence or rationale.
    needed = {ref for c in plan.continuation_candidates for ref in (c.match_event_ref, c.to_day_event_ref)}
    threads = {c.prior_thread_ref for c in plan.continuation_candidates if c.prior_thread_ref}
    material_plan = replace(plan, day_events=tuple(e for e in plan.day_events if e.ref in needed),
        prior_events=tuple(e for e in plan.prior_events if e.ref in needed),
        prior_threads=tuple(t for t in plan.prior_threads if t.ref in threads))
    events = {e.event_id: e for e in (*material_plan.day_events, *material_plan.prior_events)}
    events.update({e.event_id: e for t in material_plan.prior_threads for e in t.representative_events})
    with closing(store._connect()) as connection:
        entries = manifest(connection, events)
    messages = page_messages(SimpleNamespace(plan=material_plan, prompt_version=PAGE_PROMPT_VERSION,
                                            source_manifest=entries))
    messages[0]['content'] += (' Independently review only review_candidates, using the other candidates as '
        'competition context. Return exactly one judgment for every review candidate. An ongoing relationship, '
        'creative practice, shared theme or repeated ritual is not one occurrence. Identify the concrete unfinished '
        'task, specific prior occurrence being recalled, or sourced state transition; otherwise use separate '
        'for demonstrably distinct instances and unknown for unresolved identity. '
        'recorded_music contains authoritative source-message occurrences: pattern_key identifies a song, '
        'occurrence_key identifies one completed analysis/play occurrence. Match each event anchor to the '
        'relevant occurrence; a message can contain several occurrences. Distinct completed occurrences '
        'of one song remain separate at that event level. A broader unfinished task or an explicit later '
        'reference to the original occurrence requires its own source evidence.')
    payload = json.loads(messages[-1]['content'])
    payload['review_candidates'] = sorted(proposed)
    messages[-1]['content'] = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    selected_plan = replace(plan, continuation_candidates=tuple(c for c in plan.continuation_candidates if c.ref in proposed))
    selected = None
    if provider is None:
        from api_sources.single_source import resolve_single_source
        selected = resolve_single_source(ROUTE, require_ready=False)
    from .continuation_review_transport import source_identity as reviewer_source_identity
    source_identity = reviewer_source_identity(selected) if selected else 'injected'
    folder = Path(store.db_path).with_name('.' + Path(store.db_path).name + '.continuation-reviews')

    async def request(**kwargs):
        # Bind the actual wire, including typed occurrence evidence, not merely
        # the pre-materialization body manifest. Metadata can change alone.
        identity = {'version': VERSION, 'messages': _digest(kwargs['messages']),
                    'source': _digest(source_identity)}
        path = folder / (_digest(identity) + '.json')
        state = json.loads(path.read_text('utf-8')) if path.exists() else None
        if state is not None:
            if state.get('identity') != identity or 'raw' not in state:
                raise ContinuationReviewError('review_result_unavailable')
            if state.get('raw_digest') != _digest(state['raw']):
                raise ContinuationReviewError('review_output_changed')
            return state['raw']
        if provider is None:
            # Legacy attempts predate the source-independent ledger. A route
            # change must not turn an old unknown charge into a cache miss.
            for old_path in folder.glob('*.json'):
                if old_path.name.startswith('transport-'):
                    continue
                old = json.loads(old_path.read_text('utf-8'))
                old_identity = old.get('identity', {})
                if old_identity.get('version') == VERSION and old_identity.get('messages') == identity['messages']:
                    raise ContinuationReviewError('legacy_review_requires_explicit_recovery')
            from .continuation_review_transport import request as transport_request
            from api_sources.single_source import resolve_single_source
            backup = resolve_single_source('memory_continuation_review_backup', require_ready=False, optional=True)
            frozen = json.loads(kwargs['messages'][-1]['content'])
            def preflight():
                fresh = SourceBoundContinuationModel(source, None)._material(entries)
                if any(fresh[key] != frozen.get(key) for key in ('sources', 'event_evidence')):
                    raise ContinuationReviewError('continuation_sources_changed_before_review')
            return await transport_request(folder, kwargs, primary=selected, backup=backup,
                max_calls=max_calls if allow_call else 0, usage=usage, preflight=preflight)
        if not allow_call:
            raise ContinuationReviewError('review_budget_required')
        state = {'identity': identity, 'status': 'started'}
        atomic_write(path, state)
        try:
            raw = await provider(**kwargs)
            state.update(status='returned', raw=raw, raw_digest=_digest(raw))
            atomic_write(path, state)
            return raw
        except Exception as exc:
            state.update(status='failed', error_code=type(exc).__name__)
            atomic_write(path, state)
            raise

    schema = day_settlement_response_schema()['schema']['properties']['thread_judgments']
    raw = await SourceBoundContinuationModel(source, request)(messages=messages,
        schema_name='memory_v2_continuation_review', max_tokens=4096,
        schema={'type': 'object', 'properties': {'thread_judgments': schema},
                'required': ['thread_judgments'], 'additionalProperties': False})
    if isinstance(raw, str):
        from wander_manager.flash_structured import parse_json_object
        raw = parse_json_object(raw)
    parsed = parse_continuation_judgments(selected_plan, raw)
    if {j.candidate_ref for j in parsed} != proposed:
        raise ContinuationReviewError('review_coverage_incomplete')
    replacements = {j.candidate_ref: j for j in parsed}
    return tuple(replacements.get(j.candidate_ref, j) for j in judgments)
