"""Official Flash usage-estimate budget; unknown attempts keep their reserve."""
from datetime import datetime, timedelta
from decimal import Decimal
from deepseek_tariff import BEIJING, is_peak
from .habit_cycle_io import NotDispatched
from .habit_batches import MAX_OUTPUT


def reserve(cycle, transport, measurement):
    source = getattr(transport, 'source', {})
    if (source.get('station', {}).get('id') != 'deepseek_direct'
            or source.get('model', {}).get('id') != 'direct_flash'
            or source.get('model', {}).get('model') != 'deepseek-v4-flash-vision-exp'
            or source.get('model', {}).get('thinking') != 'off'
            or MAX_OUTPUT != 4096):
        raise NotDispatched('cycle_cost_source_not_supported')
    used = Decimal(0)
    for component in cycle['components'].values():
        for stage in component.get('stages', {}).values():
            output = stage.get('output', {})
            if output.get('request_dispatched') is False or not output:
                continue
            if output.get('cost_estimate_cny') is None:
                raise NotDispatched('cycle_cost_unknown_previous_attempt')
            used += Decimal(output['cost_estimate_cny'])
    # Conservative peak, cold-input byte allowance. Actual usage replaces it
    # only after a known return; no assumed cache hit or reasoning double count.
    amount = (Decimal(measurement['provider_bytes'] + 4096) * 2 + Decimal(4096) * 8) / 1000000
    if used + amount > Decimal(cycle['cost_limit_cny']):
        raise NotDispatched('cycle_cost_limit')
    return str(amount), datetime.now(BEIJING).isoformat()


def estimate(usage, started_at, finished_at=None):
    p, o, h = (usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens'))
    if any(type(v) is not int or v < 0 for v in (p, o, h)) or h > p:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at or started_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            return None
        start, end = start.astimezone(BEIJING), end.astimezone(BEIJING)
    except (ValueError, TypeError):
        return None
    # Dispatch is inside this interval. Crossing a tariff boundary uses the
    # higher rate until an independently bound send timestamp is available.
    peaks = [is_peak(start.isoformat()), is_peak(end.isoformat())]
    hour = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while hour < end:
        peaks.append(is_peak(hour.isoformat()))
        hour += timedelta(hours=1)
    if None in peaks:
        return None
    peak = any(peaks)
    value = (Decimal(p-h) + Decimal(h)*Decimal('.02') + Decimal(o)*4) * (2 if peak else 1) / 1000000
    return str(value)


def summary(cycle):
    known, reserved, unknown = Decimal(0), Decimal(0), 0
    for component in cycle.get('components', {}).values():
        for stage in component.get('stages', {}).values():
            output = stage.get('output', {})
            if not output or output.get('request_dispatched') is False:
                continue
            if output.get('cost_estimate_cny') is None:
                unknown += 1
                reserved += Decimal(stage.get('cost_reserve_cny', '0'))
            else:
                known += Decimal(output['cost_estimate_cny'])
    return {'limit_cny': cycle['cost_limit_cny'], 'known_usage_estimate_cny': str(known),
            'unknown_attempts': unknown, 'unknown_reserve_cny': str(reserved),
            'basis': 'usage_estimate_not_billed_cost'}
