"""Finite cooldown of transport failures; no synthetic background probes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit

_states: dict[str, tuple[float, str, str]] = {}
_lock = threading.Lock()


def _identity(source: dict) -> str:
    # Endpoint, model identity/profile and credentials form one identity.
    # Per-node thinking/schema projections do not change the station identity.
    # The digest never leaves this module.
    station, model = source.get('station', {}), source.get('model', {})
    value = [station.get('id'), station.get('base_url'), station.get('protocol'),
             [model.get('id'), model.get('model'), model.get('profile')],
             source.get('api_key', '')]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def readiness(source: dict, *, terminal: bool = False, now: float | None = None) -> dict:
    station, model = source.get('station', {}), source.get('model', {})
    reason = ''
    if not station.get('enabled', True) or not model.get('enabled', True):
        reason = 'source_disabled'
    elif not str(source.get('api_key') or '').strip():
        reason = 'api_key_not_configured'
    else:
        url = urlsplit(station.get('base_url') or '')
        if not url.hostname or url.scheme not in {'https', 'http'} or not str(model.get('model') or '').strip():
            reason = 'source_incomplete'
    if reason:
        return {'eligible': False, 'reason': reason, 'retry_at': None}
    if terminal or not source.get('availability_managed'):
        return {'eligible': True, 'reason': '', 'retry_at': None}
    moment = time.monotonic() if now is None else now
    with _lock:
        state = _states.get(_identity(source))
    if state and moment < state[0]:
        return {'eligible': False, 'reason': state[1], 'retry_at': state[2]}
    return {'eligible': True, 'reason': '', 'retry_at': None}


def record_outcome(source: dict, status: str, *, http_status: int = 0,
                   now: float | None = None) -> None:
    if not source.get('availability_managed'):
        return
    identity = _identity(source)
    # A response was received: JSON/contract errors, refusal and empty output
    # are not evidence that the station is unreachable.
    seconds = 0
    reason = ''
    if status == 'ok' or 200 <= http_status < 400:
        with _lock:
            _states.pop(identity, None)
        return
    if http_status in {401, 403, 404}:
        seconds, reason = 300, 'source_auth_or_model_unavailable'
    elif status in {'connection_error', 'timeout'} or http_status in {408, 409, 429} or http_status >= 500:
        seconds, reason = 60, 'source_connection_cooldown'
    if not seconds or status in {'refusal', 'cancelled', 'empty_content'}:
        return
    moment = time.monotonic() if now is None else now
    retry_at = (datetime.now(timezone(timedelta(hours=8))) + timedelta(seconds=seconds)).isoformat(timespec='seconds')
    with _lock:
        # Bound old credential/model identities without persisting any secret.
        for key, value in list(_states.items()):
            if value[0] <= moment:
                _states.pop(key, None)
        if len(_states) >= 1000:
            _states.pop(next(iter(_states)))
        _states[identity] = (moment + seconds, reason, retry_at)
