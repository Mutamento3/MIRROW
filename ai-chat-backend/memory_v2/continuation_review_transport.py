"""At most one primary and one explicit transport backup per frozen review.

The durable record pins both sources; configuration changes do not buy retries.
Started/unknown outcomes and semantic/output failures never trigger a backup.
"""
import json
from .day_settlement_checkpoint import _digest, atomic_write


class ReviewTransportError(RuntimeError):
    pass


def source_identity(source):
    result = {'station': {k: source['station'][k] for k in ('base_url', 'protocol')},
            'model': {k: source['model'].get(k) for k in
                      ('id', 'model', 'profile', 'json_mode', 'thinking', 'system_role', 'sampling')}}
    if source['model'].get('reasoning_effort') is not None:
        result['model']['reasoning_effort'] = source['model']['reasoning_effort']
    return result


def backup_eligible(provider):
    return (provider.get('status') in {'timeout', 'connection_error'} or
            provider.get('status') == 'http_error' and
            (provider.get('http_status') in {408, 429} or
             type(provider.get('http_status')) is int and 500 <= provider['http_status'] <= 599))


async def request(folder, kwargs, *, primary, backup=None, max_calls=1, usage=None,
                  preflight=lambda: None, invoke=None):
    from backup_files import backup_lock, BackupError
    folder.mkdir(parents=True, exist_ok=True)
    lock_name = 'review-' + _digest(kwargs['messages']) + '.lock'
    try:
        with backup_lock(folder, lock_name):
            return await _request(folder, kwargs, primary=primary, backup=backup,
                                  max_calls=max_calls, usage=usage, preflight=preflight, invoke=invoke)
    except BackupError as exc:
        raise ReviewTransportError('review_attempt_busy') from exc


async def _request(folder, kwargs, *, primary, backup, max_calls, usage, preflight, invoke):
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError('invalid_review_call_budget')
    sources = [primary] + ([backup] if backup is not None else [])
    if len({s['model']['id'] for s in sources}) != len(sources):
        raise ReviewTransportError('review_backup_duplicates_primary')
    wire = _digest(kwargs['messages'])
    identity = {'version': 'occurrence-review-transport-v1', 'messages': wire}
    path = folder / ('transport-' + _digest(identity) + '.json')
    pins = [_digest(source_identity(s)) for s in sources]
    state = json.loads(path.read_text('utf-8')) if path.exists() else {
        'identity': identity, 'sources': pins, 'attempts': []}
    if state.get('identity') != identity or state.get('sources') != pins:
        raise ReviewTransportError('review_sources_changed_requires_explicit_recovery')
    calls = 0
    for index, selected in enumerate(sources):
        attempts = state['attempts']
        if index < len(attempts):
            previous = attempts[index]
            if previous.get('status') == 'returned':
                if 'raw' not in previous or previous.get('raw_digest') != _digest(previous['raw']):
                    raise ReviewTransportError('review_output_changed')
                return previous['raw']
            if previous.get('status') != 'provider_failed' or not backup_eligible(previous.get('provider', {})):
                raise ReviewTransportError('review_result_requires_attention')
            continue
        if calls >= max_calls:
            raise ReviewTransportError('review_budget_required')
        preflight()  # Recheck full frozen source bodies before every paid request.
        if invoke is None:
            from api_sources.single_source import resolve_single_source
            route = 'memory_continuation_review' if index == 0 else 'memory_continuation_review_backup'
            current = resolve_single_source(route)
            if _digest(source_identity(current)) != pins[index]:
                raise ReviewTransportError('review_source_changed_before_request')
        attempt = {'status': 'started', 'source_id': selected['model']['id']}
        attempts.append(attempt)
        atomic_write(path, state)  # Reserving a slot precedes the actual wire.
        calls += 1
        if usage is not None:
            usage({}, started=True)
        try:
            if invoke is None:
                from api_sources.runner import run_structured_once
                from llm_client import LLM_LONG_TIMEOUT
                result = await run_structured_once(kwargs['messages'], sources=[selected],
                    route=route, task_id='continuation-review-' + _digest(identity)[:20] + '-' + str(index),
                    temperature=1.0, max_tokens=4096, enable_thinking=None,
                    request_timeout=LLM_LONG_TIMEOUT, timeout_seconds=int(LLM_LONG_TIMEOUT.read or 600),
                    json_mode=True, response_format=None, stop_on_output_error=True, inspect_provider=False)
            else:
                result = await invoke(selected, kwargs)
            provider = {'status': result.status, 'http_status': result.http_status, 'usage': result.usage}
            attempt['provider'] = provider
            if usage is not None:
                usage(result.usage)
            if result.status == 'ok':
                attempt.update(status='returned', raw=result.raw_content, raw_digest=_digest(result.raw_content))
            else:
                attempt['status'] = 'provider_failed'
            atomic_write(path, state)
            if result.status == 'ok':
                return result.raw_content
            if not backup_eligible(provider):
                raise ReviewTransportError('review_provider_' + result.status)
        except BaseException:
            # Unknown exceptions/cancellation leave the reserved started marker.
            # A saved structured provider failure remains eligible as recorded.
            raise
    raise ReviewTransportError('review_sources_exhausted')
