"""One durable attempt per cycle stage; successful transport is not approval."""
from . import habit_batches as batch
from .habit_claim_review import ROUTE as REVIEW_ROUTE
from memory_v2.day_settlement_checkpoint import _digest


class Pending(ValueError):
    pass


class NotDispatched(Pending):
    """Only explicit local preflight failures, before calling the HTTP runner."""


class Transport(batch.Transport):
    def __init__(self, source=None):
        self.explicit_source = source

    def measure(self, prompt):
        from api_sources.single_source import resolve_single_source
        self.source = self.explicit_source or resolve_single_source(
            REVIEW_ROUTE if prompt.stage == 'habit_claim_review' else batch.ROUTE, require_ready=False)
        return super().measure(prompt)

    async def __call__(self, prompt, measured):
        from api_sources.runner import run_structured_once
        from llm_client import LLM_LONG_TIMEOUT
        from llm_usage_scope import usage_metadata_scope
        try:
            if self.explicit_source is None:
                from api_sources.single_source import resolve_single_source
                resolve_single_source(REVIEW_ROUTE if prompt.stage == 'habit_claim_review' else batch.ROUTE)
            if self.measure(prompt) != measured:
                raise ValueError('changed')
        except Exception as exc:
            raise NotDispatched('cycle_local_preflight_failed') from exc
        route = REVIEW_ROUTE if prompt.stage == 'habit_claim_review' else batch.ROUTE
        with usage_metadata_scope(**prompt.transport_metadata(), cognition_run_id=prompt.run_id,
                                  source_date=prompt.source_date):
            return await run_structured_once(prompt.wire()[1], sources=[self.source], route=route,
                task_id='habit-scoped-cycle-'+measured['provider_hash'][:20], max_tokens=batch.MAX_OUTPUT,
                temperature=1.0, request_timeout=LLM_LONG_TIMEOUT,
                timeout_seconds=int(LLM_LONG_TIMEOUT.read or 600), json_mode=True,
                response_format=None, enable_thinking=None, stop_on_output_error=True, inspect_provider=False)


async def response(cycle, record, name, prompt, transport, checkpoint, *, max_calls, max_bytes):
    stages = record.setdefault('stages', {})
    wire = prompt.wire()
    binding = _digest(wire)
    saved = stages.get(name)
    measurement = transport.measure(prompt)
    if saved is not None:
        if saved['binding'] != binding or saved['measurement'] != measurement:
            raise Pending('cycle_stage_identity_changed')
        output = saved.get('output', {})
        if output.get('request_dispatched') is not False:
            if output.get('provider_status') != 'ok' or 'raw' not in output:
                raise Pending('cycle_saved_output_unavailable')
            if _digest(output['raw']) != output.get('raw_digest'):
                raise Pending('cycle_saved_output_changed')
            return output['raw']
    used = sum(s.get('output', {}).get('request_dispatched') is not False
               for r in cycle['components'].values() for s in r.get('stages', {}).values())
    if used >= max_calls:
        raise Pending('cycle_call_limit')
    if measurement['provider_bytes'] > max_bytes:
        raise Pending('cycle_complete_material_exceeds_limit')
    cost = None
    if cycle.get('cost_limit_cny') is not None:
        from .habit_cycle_cost import reserve
        cost = reserve(cycle, transport, measurement)
    if saved is None:
        saved = stages[name] = {'binding': binding, 'measurement': measurement, 'wire': wire}
    elif 'output' in saved:
        saved.setdefault('not_dispatched_history', []).append(saved['output'])
    saved['output'] = {'status': 'started'}
    if cost is not None:
        saved.update(cost_reserve_cny=cost[0], cost_started_at=cost[1])
    checkpoint()  # Claim before awaiting: cancellation is also an unknown send.
    try:
        result = await transport(prompt, measurement)
    except NotDispatched:
        saved['output'] = {'status': 'not_dispatched', 'request_dispatched': False,
                           'error_type': 'local_preflight_failed'}
        checkpoint()
        raise
    saved['output'] = {'status': 'returned', 'provider_status': result.status,
        'raw': result.raw_content, 'raw_digest': _digest(result.raw_content),
        'usage': result.usage, 'http_status': result.http_status,
        'request_dispatched': getattr(result, 'request_dispatched', None)}
    if cost is not None:
        from .habit_cycle_cost import estimate, datetime, BEIJING
        saved['cost_finished_at'] = datetime.now(BEIJING).isoformat()
        saved['output']['cost_estimate_cny'] = estimate(result.usage or {}, saved['cost_started_at'], saved['cost_finished_at'])
    checkpoint()  # Parse errors never cause a second request.
    if result.status != 'ok' or not result.raw_content or saved['output']['request_dispatched'] is False:
        raise Pending('cycle_provider_not_ok')
    return result.raw_content
