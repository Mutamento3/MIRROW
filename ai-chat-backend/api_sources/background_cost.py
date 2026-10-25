"""Cost-first recovery of day-end phases; business checkpoints stay with callers."""
import asyncio
from datetime import datetime
import time

from deepseek_tariff import BEIJING, is_peak
from .scope import phase_status, notify_wait

ROUTES = frozenset({'daily_summary', 'cognition_brief_review', 'cognition_maintenance', 'memory_encoding'})
MAX_AGE_SECONDS = 6 * 3600
RETRY_DELAYS = (300, 900)


def fallback_not_before(moment):
    clock = datetime.fromtimestamp(moment, BEIJING)
    if is_peak(clock.isoformat()) is False:
        return moment
    return clock.replace(hour=12 if clock.hour < 12 else 18, minute=0, second=0, microsecond=0).timestamp()


async def _wait(until, *, deadline, job_id, phase_id, rounds, path, route):
    from .recovery import record
    until = min(until, deadline)
    await asyncio.to_thread(record, job_id, phase_id, state='waiting', rounds=rounds,
                            expires_at=deadline, retry_after=until, path=path)
    notify_wait(route, until, phase_id)
    try:
        while time.time() < until:
            await asyncio.sleep(min(60, until - time.time()))
    finally:
        notify_wait(route, None, phase_id)


async def run(messages, *, sources, route, job_id, phase_id, saved, path, max_rounds=None,
              expires_at=None, **kwargs):
    from .recovery import record
    from .runner import run_structured_once
    from wander_manager.flash_structured import FlashJsonResult
    cheap = [s for s in sources if not s.get('stable')]
    stable = [s for s in sources if s.get('stable')]
    fresh = not saved or saved[0] in {'ok', 'not_dispatched'}
    deadline = expires_at or (time.time() + MAX_AGE_SECONDS if fresh else saved[2])
    rounds = 0 if fresh else saved[1]
    limit = min(3, max_rounds or 3)
    result = FlashJsonResult(route=route, status='error', error='retry_budget_exhausted',
                             routing_managed=True, task_id=job_id)
    result.phase_id = phase_id
    # Old retry receipts do not prove that earlier attempts were unsent.
    from .dispatch_state import combine
    request_dispatched = False if fresh else None
    if saved and (saved[0] in {'official_running', 'cheap_running'} or saved[0].startswith('official_failed')):
        result.status = 'error'
        result.error = 'previous_attempt_outcome_unknown' if saved[0].endswith('running') else 'official_attempt_already_used'
        phase_status(route, result.status, phase_id)
        return result
    if saved and saved[0] == 'refusal':
        result.status, result.error = 'refusal', 'previous_phase_refusal'
        phase_status(route, result.status, phase_id)
        return result
    if time.time() >= deadline:
        result.status, result.error = 'expired', 'background_cost_deadline'
        phase_status(route, result.status, phase_id)
        return result
    if not fresh and len(saved) > 3 and saved[3] > time.time():
        await _wait(saved[3], deadline=deadline, job_id=job_id, phase_id=phase_id,
                    rounds=rounds, path=path, route=route)
    while cheap and rounds < limit and time.time() < deadline:
        rounds += 1
        await asyncio.to_thread(record, job_id, phase_id, state='cheap_running', rounds=rounds,
                                expires_at=deadline, path=path)
        try:
            async with asyncio.timeout(max(.001, deadline - time.time())):
                result = await run_structured_once(messages, route=route, sources=cheap, task_id=job_id,
                                                  stop_on_output_error=True, **kwargs)
                request_dispatched = combine(request_dispatched, getattr(result, 'request_dispatched', None))
        except TimeoutError:
            request_dispatched = combine(request_dispatched, None)
            result.status, result.error = 'expired', 'background_cost_deadline'
        if result.status in {'ok', 'refusal', 'cancelled', 'expired'} or (
                result.status in {'json_parse_error', 'validation_error', 'empty_content'}):
            # Paid output errors use the caller's existing bounded correction,
            # never a second whole chain of generated output here.
            break
        transient = (result.status in {'timeout', 'connection_error'} or
                     result.error in {'rate_limited', 'upstream_http_error'})
        if not transient or rounds >= limit:
            break
        await _wait(time.time() + RETRY_DELAYS[min(rounds - 1, 1)], deadline=deadline,
                    job_id=job_id, phase_id=phase_id, rounds=rounds, path=path, route=route)
    else:
        if time.time() >= deadline:
            result.status, result.error = 'expired', 'background_cost_deadline'
    needs_fallback = result.status not in {'ok', 'refusal', 'cancelled', 'expired',
                                          'json_parse_error', 'validation_error', 'empty_content'}
    # If all configured cheap sources are incomplete, the unstarted default
    # result still requires the terminal source rather than fake success.
    if not cheap and time.time() < deadline:
        needs_fallback = True
    official_attempted = False
    if needs_fallback and stable:
        await _wait(fallback_not_before(time.time()), deadline=deadline, job_id=job_id,
                    phase_id=phase_id, rounds=limit, path=path, route=route)
        if time.time() < deadline:
            official_attempted = True
            await asyncio.to_thread(record, job_id, phase_id, state='official_running', rounds=limit,
                                    expires_at=deadline, path=path)
            try:
                async with asyncio.timeout(max(.001, deadline - time.time())):
                    result = await run_structured_once(messages, route=route, sources=stable, task_id=job_id,
                                                      stop_on_output_error=True, **kwargs)
                    request_dispatched = combine(request_dispatched, getattr(result, 'request_dispatched', None))
            except TimeoutError:
                request_dispatched = combine(request_dispatched, None)
                result.status, result.error = 'expired', 'background_cost_deadline'
        else:
            result.status, result.error = 'expired', 'background_cost_deadline'
    result.phase_id = phase_id
    result.request_dispatched = request_dispatched
    phase_status(route, result.status, phase_id)
    receipt_state = ('not_dispatched' if request_dispatched is False else
        'official_failed_' + result.status if official_attempted and result.status not in {'ok', 'refusal'} else result.status)
    await asyncio.to_thread(record, job_id, phase_id, state=receipt_state,
                            rounds=0 if request_dispatched is False else rounds,
                            expires_at=deadline, path=path)
    return result
