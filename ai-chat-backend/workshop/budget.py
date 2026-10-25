"""AI-only percentage estimates from explicitly exclusive calibration samples.

Account deltas are never charged to a job outside the opt-in calibration session.
References are model/window specific and frozen on each granted job budget.
"""
import json
import math
import statistics
import time
import uuid

from .protocol import quota_bucket

CALIBRATION_SECONDS = 30 * 60


def same_window(left, right):
    """Allow observed one-second endpoint rounding, never a crossed reset."""
    if any(left[key] != right[key] for key in ('bucket', 'name', 'window_mins')):
        return False
    difference = abs(left['resets_at'] - right['resets_at'])
    return difference == 0 or (difference <= 2 and time.time() < min(left['resets_at'], right['resets_at']))


def calibration_expired(budget):
    # Older one-turn approvals did not authorize a longer calibration session.
    deadline = budget.get('deadline')
    if budget.get('idle_guarded') and budget.get('baseline_pending') and deadline is None:
        return budget.get('remaining_seconds', CALIBRATION_SECONDS) <= 0
    return type(deadline) not in (int, float) or not math.isfinite(deadline) or time.time() >= deadline


def percent(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.1 <= value <= 100:
        raise ValueError('invalid_percent')
    return float(value)


def reference(db, model):
    rows = [dict(row) for row in db.execute('SELECT * FROM budget_samples WHERE model=? ORDER BY rowid DESC LIMIT 20', (model,))]
    if not rows:
        return None
    latest = rows[0]
    matched = [row for row in rows if row['window_mins'] == latest['window_mins'] and row['bucket'] == latest['bucket']][:5]
    return {'tokens_per_percent': statistics.median(row['token_delta']/row['percent_delta'] for row in matched),
        'window_mins': latest['window_mins'], 'bucket': latest['bucket'], 'sample_count': len(matched),
        'status': 'provisional' if len(matched) < 3 else 'multiple_samples', 'observed_at': latest['created_at'],
        'source': '人类伙伴声明不并行使用其他 Codex 时，实际 AI 工单的 token 与官方额度回执'}


def references(store):
    from .store import MODELS
    with store.db() as db:
        return {model: reference(db, model) for model in MODELS}


def window(payload, model, expected=None):
    key, bucket = quota_bucket(payload, model)
    windows = []
    for name in ('primary', 'secondary'):
        row = bucket.get(name)
        if not isinstance(row, dict):
            continue
        used, minutes, reset = row.get('usedPercent'), row.get('windowDurationMins'), row.get('resetsAt')
        if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100 or type(minutes) is not int or minutes <= 0 or type(reset) is not int:
            continue
        item = {'bucket': key, 'name': name, 'used': float(used), 'window_mins': minutes, 'resets_at': reset}
        if expected and (key != expected['bucket'] or name != expected['name'] or minutes != expected['window_mins']):
            continue
        windows.append(item)
    if not windows:
        raise ValueError('calibration_quota_unavailable')
    return max(windows, key=lambda row: row['window_mins'])


def initialize(db, job, cfg):
    # A started legacy budget is retained until a human explicitly grants %.
    if job['token_cap'] or cfg['budget_mode'] == 'tokens':
        return job.get('budget') or {}, job['token_cap'] or cfg['token_budget']
    ref = reference(db, job['model'] or cfg['model'])
    if not ref:
        return {'mode': 'percent', 'status': 'uncalibrated', 'target_percent': cfg['target_percent']}, 0
    limit = percent(cfg['target_percent'])
    return {'mode': 'percent', 'status': 'estimated', 'reference': ref, 'limit_percent': limit}, math.ceil(ref['tokens_per_percent'] * limit)


def summary(job, *, limits_enabled=True):
    if not limits_enabled:
        return {'mode':'observe_only','status':'observing','note':'链路测试中：制作与质检预算限制暂时关闭，实际用量仍记录；开工方式遵循工作坊设置，账号开工门槛仍有效。'}
    budget = job.get('budget') or {}
    if budget.get('mode') == 'calibrating':
        return {'mode': 'calibrating', 'status': 'calibrating', 'additional_percent': budget['additional_percent'],
                'window_mins': budget['start']['window_mins'], 'deadline':budget.get('deadline'),
                'starts_at':budget.get('not_before') if budget.get('baseline_pending') else None,
                'idle_guarded':bool(budget.get('idle_guarded')), 'remaining_seconds':budget.get('remaining_seconds'),
                'completed_turns':budget.get('completed_turns', 0),
                'note': '在获批的最长 30 分钟内累计真实工作；样本不足不强停正常交付，不额外调用模型凑样本。期间暂不并行使用其他 Codex。'}
    ref = budget.get('reference')
    if budget.get('mode') == 'percent' and ref:
        return {'mode': 'percent', 'status': 'estimated', 'used_percent': round(job['tokens']/ref['tokens_per_percent'], 2),
                'limit_percent': round(job['token_cap']/ref['tokens_per_percent'], 2), 'sample_count': ref['sample_count'],
                'window_mins': ref['window_mins'], 'reference_status': ref['status'],
                'note': '只按本工单 token 估算，不是官方逐工单额度；参考固定，桌面用量不计入。'}
    if budget.get('mode') == 'percent':
        return {'mode': 'percent', 'status': 'uncalibrated', 'target_percent': budget.get('target_percent'),
                'note': '当前模型尚无独立校准样本，百分比暂不可估算；手动批准时可校准。'}
    return {'mode': 'tokens', 'status': 'legacy', 'note': '旧工单保留原 token 预算；可用百分比批准追加。'}


async def approve_percent(service, job_id, addition, mode, *, calibration_exclusive=False, calibration_delay_seconds=0):
    from .protocol import quota_remaining
    from .store import now
    addition = percent(addition)
    if type(calibration_delay_seconds) is not int or not 0 <= calibration_delay_seconds <= 60:
        raise ValueError('invalid_calibration_delay')
    if mode not in ('continue', 'discuss'):
        raise ValueError('invalid_approval_mode')
    async with service.mutation:
        job = service.store.job(job_id)
        if job['state'] != 'budget_paused' or not job['usage_known']:
            raise ValueError('known_paused_usage_required')
        model = job['model'] or service.store.settings()['model']
        with service.store.db() as db:
            ref = (job.get('budget') or {}).get('reference') or reference(db, model)
        if ref:
            cap = max(job['tokens'], job['token_cap']) + math.ceil(addition*ref['tokens_per_percent'])
            budget = {'mode': 'percent', 'status': 'estimated', 'reference': ref, 'limit_percent': cap/ref['tokens_per_percent']}
        else:
            if calibration_exclusive is not True:
                raise ValueError('需要先校准：请确认期间不并行使用桌面或其他 Codex。')
            if hasattr(service, 'pool') and service.pool.active():
                raise ValueError('请先等工作坊正在制作或质检的工单结束，再开始独占校准。')
            if any(row['id'] != job_id and (row.get('budget') or {}).get('mode')=='calibrating' for row in service.store.jobs()):
                raise ValueError('已有另一份工单正在独占校准，请等该次校准结束。')
            metadata = await service.runner.metadata(model)
            if hasattr(service, 'pool') and service.pool.active():
                raise ValueError('额度核对期间有工单开工，请等它结束后再开始独占校准。')
            payload = metadata.get('quota') or {}
            start = window(payload, model)
            remaining = quota_remaining(payload, model)
            if remaining is None or remaining < service.store.settings()['remaining_floor']:
                raise ValueError('账号剩余额度不足或未知，暂不能开始校准。')
            budget = {'mode': 'calibrating', 'status': 'calibrating', 'start': start, 'begin_tokens': job['tokens'],
                      'additional_percent': addition, 'exclusive_attested': True, 'approved_at': now(),
                      'deadline':time.time()+CALIBRATION_SECONDS, 'completed_turns':0}
            if service.store.settings()['desktop_idle_only']:
                budget.update(idle_guarded=True, baseline_pending=True, not_before=time.time(),
                    deadline=None, remaining_seconds=CALIBRATION_SECONDS,
                    cap_base_tokens=job['tokens'], cap_percent=addition)
            if calibration_delay_seconds:
                budget.update(not_before=time.time()+calibration_delay_seconds, baseline_pending=True)
            # This is a disclosed emergency ceiling, not a claimed % conversion.
            cap = job['tokens'] + 10_000_000
        receipt_text = f'[工作坊资源回执；人类伙伴批准，非 AI 发言]\n本次追加 {addition:g}% 已批准，资源暂停已解除；以房间中当前需求版本的讨论、方案和补充为准继续。'
        pending = (job['pending_text'] + '\n' + receipt_text).strip()
        fields = {'model': model, 'token_cap': cap, 'budget': budget, 'state': 'queued', 'reason': '', 'pending_text': pending}
        if mode == 'discuss':
            fields.update(confirmed_version=0, resume_state='', pending_text=(pending + '\n[人类伙伴的资源批准：重新讨论方向]\n请先重新讨论范围，AI 再次确认方案后才制作。').strip())
        service.store.update(job_id, **fields)
        service.store.event(job_id, 'resource_approval', {'source': 'human_settings', 'additional_percent': addition,
            'budget_mode': budget['mode'], 'consumed': job['tokens'], 'token_cap': cap,
            'summary': '人类伙伴批准百分比追加；独立估算' if ref else '人类伙伴批准最长 30 分钟的真实工作校准阶段；样本不足不强停正常交付，不额外空跑。'})
    service.wake.set()


def calibration_stop(job, payload):
    budget = job.get('budget') or {}
    if budget.get('mode') != 'calibrating':
        return None
    if calibration_expired(budget):
        return 'calibration_expired'
    try:
        current = window(payload, job['model'], budget['start'])
    except ValueError:
        return 'calibration_unavailable'
    delta = current['used'] - budget['start']['used']
    if not same_window(current, budget['start']) or delta < 0:
        return 'calibration_reset'
    return 'calibration_limit' if delta >= budget['additional_percent'] else None


def finish_calibration(store, job_id, payload):
    if not store.settings()['budget_limits_enabled']:
        return
    from .store import now
    job = store.job(job_id)
    budget = job.get('budget') or {}
    if budget.get('mode') != 'calibrating':
        return
    if job['reason'] in ('desktop_busy','desktop_unknown'):
        wait_for_desktop(store, job_id)
        return
    # A later quota read cannot erase the receipt that stopped this turn.
    # The account endpoint may return different snapshots at checkpoint/end.
    stopped_reason = budget.get('stop_reason')
    if job['state'] in ('stopping', 'budget_paused') and job['reason'] in (
            'calibration_limit', 'calibration_reset', 'calibration_unavailable', 'calibration_expired'):
        stopped_reason = job['reason']
    reason = stopped_reason or calibration_stop(job, payload)
    ref = None
    token_delta = job['tokens'] - budget['begin_tokens']
    delta = None
    try:
        end = window(payload, job['model'], budget['start'])
        delta = end['used'] - budget['start']['used']
        stopped_window = budget.get('stop_window')
        if stopped_window and (not same_window(end, stopped_window) or end['used'] < stopped_window['used']):
            reason = 'calibration_reset'
        if not job['usage_known']:
            reason = 'usage_unknown'
        if reason not in (None, 'calibration_limit') or delta < 1 or token_delta <= 0:
            raise ValueError('calibration_sample_insufficient_or_reset')
        with store.db() as db:
            db.execute('INSERT INTO budget_samples VALUES(?,?,?,?,?,?,?)',
                (uuid.uuid4().hex, job['model'], end['bucket'], end['window_mins'], token_delta, delta, now()))
            ref = reference(db, job['model'])
    except ValueError:
        pass
    if not ref and reason is None:
        # A normal reply/delivery must not be converted to budget_paused simply
        # because the official integer percentage has not moved yet. Keep the
        # original baseline through subsequent, independently needed turns.
        store.update(job_id, budget={**budget, 'completed_turns':budget.get('completed_turns',0)+1})
        store.event(job_id, 'budget_calibration', {'model':job['model'], 'token_delta':token_delta,
            'percent_delta':delta, 'status':'collecting', 'sample_count':0,
            'summary':'窗口变化暂不足 1 个百分点；在原批准时限与上限内继续累计真实工作，正常交付与确认不暂停。'})
        return
    if ref:
        cap = budget.get('cap_base_tokens', budget['begin_tokens']) + math.ceil(
            budget.get('cap_percent', budget['additional_percent'])*ref['tokens_per_percent'])
        fields = {'budget': {'mode': 'percent', 'status': 'estimated', 'reference': ref, 'limit_percent': cap/ref['tokens_per_percent']}, 'token_cap': cap}
        if job['tokens'] >= cap:
            fields.update(state='stopping', reason='token_budget')
    else:
        fields = {'budget': {'mode':'percent', 'status':'uncalibrated', 'target_percent':budget['additional_percent']},
                  'token_cap':job['tokens'],
                  'state':'budget_paused' if job['state'] == 'budget_paused' else 'stopping',
                  'reason':reason or 'calibration_required'}
    # Completion of a sample never overrides AI's pause/cancel/delete decision.
    if job['state'] == 'stopping' and job['reason'] not in ('token_budget', 'calibration_limit', 'calibration_reset', 'calibration_unavailable', 'calibration_expired'):
        fields.pop('state', None)
        fields.pop('reason', None)
    store.update(job_id, **fields)
    store.event(job_id, 'budget_calibration', {'model':job['model'], 'token_delta':token_delta,
        'percent_delta':delta, 'reason':reason,
        'stop_window':budget.get('stop_window'),
        'status':'reference_ready' if ref else 'invalid_sample', 'sample_count':ref['sample_count'] if ref else 0,
        'summary':'已保存独立参考；以后只按 AI 工单用量估算。' if ref else '校准时限、获批增量或回执条件触碰边界；未生成参考，现场保留。'})


def record_calibration_stop(store, job_id, reason, payload):
    """Preserve the bounded official snapshot that triggered a resource stop."""
    job = store.job(job_id)
    budget = job.get('budget') or {}
    if budget.get('mode') != 'calibrating' or not reason.startswith('calibration_') or budget.get('stop_reason'):
        return
    try:
        observed = window(payload, job['model'], budget['start'])
    except ValueError:
        observed = None
    store.update(job_id, budget={**budget, 'stop_reason':reason, 'stop_window':observed})
    store.event(job_id, 'budget_calibration', {'status':'checkpoint_stopped', 'reason':reason,
        'start_window':budget['start'], 'stop_window':observed,
        'summary':'已保留触发校准暂停的官方窗口回执；结束时的另一份回执不会覆盖暂停依据。'})


def wait_for_desktop(store, job_id):
    """Discard an overlap epoch; retain the same resource grant and own usage."""
    job = store.job(job_id)
    budget = job.get('budget') or {}
    if budget.get('mode') != 'calibrating' or budget.get('baseline_pending'):
        return
    remaining = max(0,(budget.get('deadline') or time.time())-time.time())
    replacement = {**budget, 'idle_guarded':True, 'baseline_pending':True, 'not_before':time.time(),
        'deadline':None, 'remaining_seconds':remaining,
        'cap_base_tokens':budget.get('cap_base_tokens',budget['begin_tokens']),
        'cap_percent':budget.get('cap_percent',budget['additional_percent'])}
    replacement.pop('fixed_deadline',None)
    # Desktop's intervening account delta is not a debit to AI's grant.
    # Rebase sampling only; the cap still charges ALL previous job tokens.
    replacement.pop('ceiling_window',None)
    replacement['additional_percent'] = replacement['cap_percent']
    store.update(job_id, budget=replacement)
    store.event(job_id,'budget_calibration',{'status':'waiting_desktop', 'revision':job['revision'],
        'token_delta':job['tokens']-budget['begin_tokens'], 'remaining_seconds':round(remaining),
        'summary':'桌面任务介入，本段不生成独立参考；实际工单用量保留，原批准增量和剩余工作时限不增加，空闲后重取采样起点。'})


def settle_calibrations(store):
    """Close ended/expired approvals without hiding a finished delivery or plan."""
    if not store.settings()['budget_limits_enabled']:
        return
    for job in store.jobs():
        budget = job.get('budget') or {}
        if budget.get('mode') == 'calibrating' and job['state'] == 'budget_paused' and job['reason'] in (
                'calibration_reset', 'calibration_unavailable', 'calibration_limit', 'calibration_expired'):
            finish_calibration(store, job['id'], {})
            continue
        ended = job['state'] in ('awaiting_review','accepted','canceled')
        if budget.get('mode') != 'calibrating' or not (ended or calibration_expired(budget)):
            continue
        if job['state'] in ('executing', 'discussing', 'stopping', 'recovery_required'):
            continue  # The active runner owns its stop/completion receipt.
        fields = {'budget':{'mode':'percent', 'status':'uncalibrated', 'target_percent':budget['additional_percent']},
                  'token_cap':job['tokens']}
        if job['state'] in ('queued','account_paused'):
            fields.update(state='budget_paused', reason='calibration_expired')
        store.update(job['id'], **fields)
        store.event(job['id'], 'budget_calibration', {'status':'closed' if ended else 'expired',
            'reason':'work_phase_ended' if ended else 'calibration_expired',
            'summary':'本阶段工作已结束，校准样本暂不足；正常交付与验收保留，无需为了校准继续消耗。' if ended else
                '校准阶段已到时限且样本不足；已有方案、交付和验收状态保留，继续模型工作需重新批准。'})
