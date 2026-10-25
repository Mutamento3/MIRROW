"""Expose actual low-cost waiting without reporting stalled work or completion."""
from datetime import datetime
from deepseek_tariff import BEIJING


def update_wait(state, route, until, phase_id=None):
    if state is None:
        return
    waits = state.setdefault('routing_wait_phases', {})
    key = phase_id or route
    if until is not None:
        waits[key] = {'route': route, 'until': until}
    else:
        waits.pop(key, None)
    if waits:
        next_check = datetime.fromtimestamp(min(w['until'] for w in waits.values()), BEIJING).isoformat()
        stamp = datetime.fromtimestamp(max(w['until'] for w in waits.values()), BEIJING).isoformat()
        state['routing_wait'] = {'retry_after': stamp, 'next_check': next_check, 'reason': 'cost_first_recovery'}
        state['message'] = '低价线路暂不可用，保留当前进度等待恢复或谷时兜底；下次检查 ' + next_check[11:16]
        state.pop('stalled', None)
    else:
        state.pop('routing_wait_phases', None)
        state.pop('routing_wait', None)
        if str(state.get('message') or '').startswith('低价线路暂不可用，保留当前进度'):
            state['message'] = '继续处理当前未完成阶段'
        state['last_progress'] = datetime.now(BEIJING).replace(tzinfo=None).isoformat()
