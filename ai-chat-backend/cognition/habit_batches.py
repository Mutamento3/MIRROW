"""Opt-in habit-only extraction, one complete request per durable component.

This prepares reports. Existing fact/claim reviewers and book CAS remain the
only approval/commit authorities. Legacy joint runs never enter this module.
"""
import json
from . import habit_review, habit_local_contract, books
from .maintenance_sources import model_materials
from .world_materials import numbered_references
from .prompt_request import CognitionPrompt
from memory_v2.day_settlement_checkpoint import _digest

MODE = 'habit-context-batches-v1'
ROUTE = 'cognition_habit_extract'
MAX_PROVIDER_BYTES = 125000
MAX_NEW_CALLS = 4
MAX_OUTPUT = 4096


def component(request, space, snapshot, proposals, *, source_date, run_id, canonical_payload=False):
    known = {e['id']: e for e in space}
    required = set(request['reference_ids'])
    if not required <= known.keys():
        raise ValueError('habit_component_source_missing')
    targets = habit_local_contract.choices(request, snapshot)
    names = {(t['target']['domain'], t['name']) for t in targets}
    relevant = [p for p in proposals if (p.get('domain'), p.get('name')) in names]
    # Preserve complete dependencies of prior proposals, but they do not expand
    # this request's allowed evidence IDs.
    for proposal in relevant:
        required.update(e['message_id'] for e in proposal.get('evidence', []))
    if not required <= known.keys():
        raise ValueError('habit_proposal_source_missing')
    selected = [e for e in space if e['id'] in required]
    n_of = {e['id']: n for n, e in enumerate(selected, 1)}
    payload = {'source_date': source_date, 'messages': model_materials(selected),
        'habit_review_requests': habit_review.model_requests([request], n_of, local=True, snapshot=snapshot),
        'existing_proposals': numbered_references(relevant, n_of, {e['id']: e['text'] for e in selected})}
    prompt = CognitionPrompt(habit_review.extraction_contract([request]) +
        '\n本次只生成所列请求的 habit_reviews；existing_proposals 是已生成提议，本阶段不生成提议或批准。',
        payload, stage='habit_extract', source_date=source_date, run_id=run_id,
        canonical_payload=canonical_payload)
    return prompt, selected


class Transport:
    """One configured source; no implicit fallback and no automatic resend."""
    def __init__(self):
        from api_sources.single_source import resolve_single_source
        self.source = resolve_single_source(ROUTE)

    def measure(self, prompt):
        from api_sources.adapters import build_request
        from api_sources.model_context import with_current_model
        from api_sources.structured_output import prompt_schema_messages
        from context_builder.channel_projection import prepare
        from memory_v2.continuation_review_transport import source_identity
        messages = prompt_schema_messages(self.source,
            with_current_model(prompt.wire()[1], self.source['model']['model']), json_mode=True, response_format=None)
        messages, _ = prepare(messages)
        body = build_request(self.source, messages, temperature=1.0, max_tokens=MAX_OUTPUT,
                             json_mode=True, enable_thinking=None, response_format=None)[1]
        return {'provider_bytes': len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()),
                'provider_hash': _digest(body), 'source_pin': _digest(source_identity(self.source)),
                'source_id': self.source['model']['id'], 'max_output_requested': MAX_OUTPUT}

    async def __call__(self, prompt, measured):
        if self.measure(prompt) != measured:
            raise ValueError('habit_provider_material_changed')
        from api_sources.runner import run_structured_once
        from llm_client import LLM_LONG_TIMEOUT
        from llm_usage_scope import usage_metadata_scope
        with usage_metadata_scope(**prompt.transport_metadata(), cognition_run_id=prompt.run_id,
                                  source_date=prompt.source_date):
            return await run_structured_once(prompt.wire()[1], sources=[self.source], route=ROUTE,
                task_id=MODE + '-' + measured['provider_hash'][:20], max_tokens=MAX_OUTPUT,
                temperature=1.0, request_timeout=LLM_LONG_TIMEOUT,
                timeout_seconds=int(LLM_LONG_TIMEOUT.read or 600), json_mode=True,
                response_format=None, enable_thinking=None, stop_on_output_error=True, inspect_provider=False)


def check_sources(requests, space):
    from .habit_claim_review import check_current_private_sources
    from .habit_baseline_sources import check_current
    if any(r.get('source_coverage', {}).get('context', {}).get('lookup_status') == 'unavailable' for r in requests):
        raise ValueError('habit_context_unavailable')
    check_current_private_sources(space)
    check_current(requests)


async def extract(state, requests, space, snapshot, proposals, checkpoint, *, source_date, run_id,
                  transport=None, max_new_calls=MAX_NEW_CALLS, max_provider_bytes=MAX_PROVIDER_BYTES):
    if type(max_new_calls) is not int or not 0 <= max_new_calls <= 24:
        raise ValueError('habit_batch_call_limit_invalid')
    if type(max_provider_bytes) is not int or not 1 <= max_provider_bytes <= 250000:
        raise ValueError('habit_batch_size_limit_invalid')
    batch = state.setdefault('habit_report_batches', {'version': MODE, 'components': {}})
    if batch['version'] != MODE:
        raise ValueError('habit_batch_version_changed')
    records = batch['components']
    used = sum('output' in row and row['output'].get('request_dispatched') is not False for row in records.values())
    reports, deferred = [], []
    for request in requests:
        rid = request['id']
        try:
            prompt, selected = component(request, space, snapshot, proposals, source_date=source_date, run_id=run_id)
            binding = _digest({'request': request, 'wire': prompt.wire(), 'sources': selected})
            record = records.get(rid)
            if record is not None and record['binding'] != binding:
                raise ValueError('habit_component_binding_changed')
            if record is None:
                record = {'binding': binding, 'status': 'prepared', 'request': request,
                          'space': selected, 'wire': prompt.wire()[1]}
                records[rid] = record
                checkpoint()
            check_sources([request], selected)
            if 'output' not in record or record['output'].get('request_dispatched') is False:
                if used >= max_new_calls:
                    deferred.append({'id': rid, 'reason': 'batch_call_limit'})
                    continue
                if transport is None:
                    transport = Transport()
                measured = transport.measure(prompt)
                if measured['provider_bytes'] > max_provider_bytes:
                    record.update(status='deferred', reason='complete_component_exceeds_size_limit', measurement=measured)
                    deferred.append({'id': rid, 'reason': record['reason']})
                    continue
                if 'output' in record:
                    record.setdefault('not_dispatched_history', []).append(
                        {'measurement': record['measurement'], 'output': record['output']})
                record.update(status='started', measurement=measured, output={'status': 'started'})
                checkpoint()  # Persist before dispatch, including cancellations.
                used += 1
                result = await transport(prompt, measured)
                record['output'] = {'status': 'returned', 'provider_status': result.status,
                    'raw': result.raw_content, 'usage': result.usage, 'http_status': result.http_status,
                    'request_dispatched': getattr(result, 'request_dispatched', None),
                    'error': getattr(result, 'error', None)}
                checkpoint()  # Complete raw response survives parsing/commit failure.
                if record['output']['request_dispatched'] is False:
                    used -= 1
            output = record['output']
            if output.get('request_dispatched') is False:
                record['status'] = 'not_dispatched'
                deferred.append({'id': rid, 'reason': 'not_dispatched'})
                continue
            if output.get('status') != 'returned' or output.get('provider_status') != 'ok':
                refused = output.get('status') == 'returned' and output.get('provider_status') == 'refusal'
                record['status'] = 'refused' if refused else 'pending'
                deferred.append({'id': rid, 'reason': 'provider_refusal' if refused else 'previous_output_unavailable'})
                continue
            check_sources([request], selected)
            assessment = habit_review.report_assessment(output['raw'], [request], selected, snapshot)
            record.update(status='completed' if assessment['reports'] else 'incomplete',
                          reports=assessment['reports'], rejections=assessment['rejections'])
            reports.extend(assessment['reports'])
        except Exception as exc:
            deferred.append({'id': rid, 'reason': 'component_unavailable', 'error_type': type(exc).__name__})
        finally:
            checkpoint()
    state['habit_review_reports'] = reports
    batch.update(status='completed' if len(reports) == len(requests) else 'incomplete',
        requested_count=len(requests), recovered_count=len(reports), dispatched_count=used,
        deferred=deferred, rejections=[r for c in records.values() for r in c.get('rejections', [])])
    checkpoint()
