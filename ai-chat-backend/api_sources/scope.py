"""Freeze routing authority for a business invocation, never its raw context."""
from contextvars import ContextVar
from functools import wraps
import asyncio
from datetime import datetime, timedelta, timezone
import uuid

_SNAPSHOT = ContextVar("api_routing_snapshot", default=None)
_JOB = ContextVar("api_business_job", default=None)
_FAILURES = ContextVar("api_phase_failures", default=None)
_NOW = ContextVar("api_routing_start_time", default=None)
_WAIT_CALLBACK = ContextVar('api_routing_wait_callback', default=None)


def notify_wait(route, until, phase_id=None):
    callback = _WAIT_CALLBACK.get()
    if callback:
        callback(route, until, phase_id)


def selection_time():
    return _NOW.get()


def current_job():
    return _JOB.get()


def phase_status(route, status, phase_id=None):
    failures = _FAILURES.get()
    if failures is not None:
        key = phase_id or route
        if status == "ok":
            failures.pop(key, None)
        else:
            failures[key] = status


def failed_phases():
    return dict(_FAILURES.get() or {})


def phase_failure_message(failures):
    """Describe the recorded cause without inferring quota or provider health."""
    reasons = {
        'json_parse_error': '模型返回不是完整有效的 JSON',
        'validation_error': '返回结构未通过校验',
        'empty_content': '模型返回为空',
        'timeout': '请求超时，结果待核对',
        'connection_error': '连接失败',
        'authentication_error': '来源认证失败',
        'refusal': '模型拒绝本次请求',
        'expired': '本轮等待期限已到',
        'cancelled': '请求已取消',
    }
    labels = {'cognition_maintenance': '认知整理', 'daily_summary': '日总结',
              'cognition_brief_review': '认知简版审核', 'memory_encoding': '记忆编码'}
    details = sorted({labels.get(key.split(':', 1)[0], '模型阶段') + '：' +
                      reasons.get(status, '调用未成功，已保存结果保留')
                      for key, status in failures.items()})
    return '部分模型阶段未完成；' + '；'.join(details)


def current_snapshot():
    return _SNAPSHOT.get()


def routing_scope(function):
    @wraps(function)
    async def scoped(*args, **kwargs):
        tracked = function.__name__ == "_run_tracked"
        if _SNAPSHOT.get() is not None and not tracked:
            return await function(*args, **kwargs)
        from .config import load_config
        snapshot = await asyncio.to_thread(load_config)
        token = _SNAPSHOT.set(snapshot)
        day = kwargs.get("source_date") or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        job_token = _JOB.set(f"{day}:{args[0]}" if tracked else uuid.uuid4().hex)
        failures_token = _FAILURES.set({})
        time_token = _NOW.set(datetime.now(timezone(timedelta(hours=8))))
        try:
            return await function(*args, **kwargs)
        finally:
            _SNAPSHOT.reset(token)
            _JOB.reset(job_token)
            _FAILURES.reset(failures_token)
            _NOW.reset(time_token)
    return scoped
