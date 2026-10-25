"""Private, bounded in-memory context inspection. Never writes prompt bodies.

Builder assembly and provider submission are distinct observations. Matching
requires exact message digests, not scenario names or approximate timestamps.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
import time
import ipaddress
from contextvars import ContextVar
from datetime import datetime

_LOCK = threading.RLock()
_BUILDS = {}
_MAX_BUILDS = 6
_MAX_CHARS = 2_000_000
_TTL_SECONDS = 30 * 60
_ACTIVE_BUILD = ContextVar('context_inspection_build', default='')


def require_private_inspection(request):
    """Keep diagnostic bodies private even when general dev access is open."""
    from fastapi import HTTPException
    from network_access import NetworkAccessPolicy
    from shared_state import get_allowed_ips
    host = request.client.host if request.client else None
    try:
        if host and ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    policy = NetworkAccessPolicy.from_environment(get_allowed_ips())
    if host and (policy.exact_ips or policy.mobile_lan_mac) and policy.allows(host):
        return
    raise HTTPException(status_code=403, detail='仅限已配置的私有诊断设备')


def bind_build(call_id):
    return _ACTIVE_BUILD.set(call_id or '')


def reset_build(token):
    _ACTIVE_BUILD.reset(token)


def redact(value):
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+\S+|\bsk-[\w-]+", "[凭据已隐藏]", value)
        value = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[凭据已隐藏]', value)
        value = re.sub(r"(?:https?|wss?)://[^\s<>\"']+", "[地址已隐藏]", value)
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "[地址已隐藏]", value)
        def ipv6(match):
            try:
                ipaddress.IPv6Address(match.group(0))
                return '[地址已隐藏]'
            except ValueError:
                return match.group(0)
        value = re.sub(r'(?<![\w:])[a-fA-F0-9]*:[a-fA-F0-9:]+(?![\w:])', ipv6, value)
        return value
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        if value.get('type') in {'image_url', 'input_image', 'image'}:
            return {'type': value['type'], 'diagnostic_note': '图像载荷已隐藏，原消息仍包含此内容块'}
        return {str(key): ('[凭据已隐藏]' if re.search(r'(?i)^(api[_-]?key|access[_-]?token|token|secret|password|authorization)$', str(key)) else redact(item))
                for key, item in value.items() if not str(key).startswith('_')}
    return value if isinstance(value, (int, float, bool)) or value is None else '[非文本对象]'


def _signature(message):
    from prompt_ledger import _provider_message_fields, _value_digest
    return _value_digest(_provider_message_fields(message))


def _expire():
    for key in list(_BUILDS):
        if time.monotonic() - _BUILDS[key]['created'] > _TTL_SECONDS:
            del _BUILDS[key]


def observe_build(*, call_id, recipe, scenario, messages, sections, dynamic_last):
    """Called with the actual Builder output, before private markers are removed."""
    if not call_id:
        return
    try:
        encoded = json.dumps([messages, sections], ensure_ascii=False, default=str)
        snapshot = {
            'call_id': call_id, 'recipe': recipe, 'scenario': scenario,
            'timestamp': datetime.now().isoformat(), 'stage': 'builder',
            'dynamic_last': bool(dynamic_last), 'providers': [],
            'body_available': len(encoded) <= _MAX_CHARS,
            'messages': redact(messages) if len(encoded) <= _MAX_CHARS else [],
            'sections': redact(sections) if len(encoded) <= _MAX_CHARS else [],
            'body_note': '凭据、地址和图像载荷被隐藏；其余文本不截断' if len(encoded) <= _MAX_CHARS else '快照超过内存上限，未保留正文，不能作为完整预览',
        }
        with _LOCK:
            _expire()
            first = messages[0].get('content') if messages and messages[0].get('role') == 'system' else None
            first_proof = (len(first), hashlib.sha256(first.encode('utf-8')).hexdigest()) if isinstance(first, str) and first else None
            _BUILDS[call_id] = {'snapshot': snapshot, 'signature': [_signature(m) for m in messages], 'first_proof': first_proof, 'created':time.monotonic()}
            while len(_BUILDS) > _MAX_BUILDS:
                del _BUILDS[next(iter(_BUILDS))]
    except Exception:
        # Diagnostics must never affect the prompt or resource claims.
        return


def observe_provider(messages, *, model='', provider='', use_bound=True,
                     source_messages=None, request_projection=None, request_id=None):
    """Capture serialized messages immediately before submission, without headers.

    Explicit call IDs prove request lineage, NOT message completeness. Without
    that lineage, only a unique exact ordered prefix can establish association.
    """
    try:
        signatures = [_signature(m) for m in messages]
        verified_projection = False
        if source_messages is not None and request_projection:
            if str(request_projection.get('variant', '')).startswith('channel_'):
                from context_builder.channel_projection import verify_projection
            elif str(request_projection.get('variant', '')).startswith('wander_'):
                from wander_manager.request_projection import verify_projection
            else:
                from behavior_scheduler.pro_request_adapter import verify_projection
            verified_projection = verify_projection(source_messages, messages, request_projection)
        source_signatures = [_signature(m) for m in source_messages] if verified_projection else signatures
        with _LOCK:
            _expire()
            bound = _ACTIVE_BUILD.get() if use_bound else ''
            candidates = [(key, item) for key, item in _BUILDS.items()
                          if item['signature'] and source_signatures[:len(item['signature'])] == item['signature']]
            if bound:
                candidates = [(bound, _BUILDS[bound])] if bound in _BUILDS else []
            # Identical builds are ambiguous: do not attach to the newest by time.
            if len(candidates) != 1:
                return None
            call_id, build = candidates[0]
            available = len(json.dumps(messages, ensure_ascii=False, default=str)) <= _MAX_CHARS
            request_id = request_id or uuid.uuid4().hex
            event = {'request_id': request_id, 'timestamp': datetime.now().isoformat(),
                     'model': redact(model), 'provider': provider, 'status': 'submitted',
                     'body_available': available, 'messages': redact(messages) if available else [],
                     'association': 'explicit_call_id' if bound else 'exact_unique_prefix',
                     'message_count': len(messages)}
            if request_projection:
                event['provider_variant'] = request_projection.get('variant', '')
                event['projection_verified'] = verified_projection
            mapping = []
            cursor = 0
            for index, signature in enumerate(build['signature']):
                found = next((pos for pos in range(cursor,len(source_signatures)) if source_signatures[pos] == signature),None)
                kind = 'exact'
                provider_index = found + 1 if found is not None else None
                if found is not None and verified_projection:
                    row = next((row for row in request_projection.get('source_projection_map', [])
                                if row.get('source_index') == found + 1), None)
                    # Native protocols can merge several source carriers. Full
                    # wire verification remains valid without inventing a
                    # single provider index for each Builder message.
                    kind = row['match'] if row else 'verified_wire_material_no_single_carrier'
                    provider_index = row.get('provider_index') if row else None
                if found is None and index == 0 and build['snapshot']['messages'] and messages:
                    proof = build.get('first_proof')
                    actual = messages[0].get('content')
                    if (proof and messages[0].get('role') == 'system' and isinstance(actual,str)
                            and _signature({**messages[0], 'content': actual[:proof[0]]}) == build['signature'][0]):
                        found, kind, provider_index = 0, 'extended_system', 1
                mapping.append({'builder_index':index+1,'provider_index':provider_index,'match':kind if found is not None else 'not_matched'})
                if found is not None:
                    cursor = found+1
            event['builder_message_map'] = mapping
            build['snapshot']['providers'].append(event)
            build['snapshot']['providers'] = build['snapshot']['providers'][-3:]
            return call_id, request_id
    except Exception:
        return None


def finish_provider(handle, status, error_type=None):
    if not handle:
        return
    with _LOCK:
        build = _BUILDS.get(handle[0])
        if build:
            for event in build['snapshot']['providers']:
                if event['request_id'] == handle[1]:
                    if error_type:
                        event['error_type'] = redact(error_type)
                        event['status'] = 'response_parse_error' if event['status'] == 'response_received' else 'provider_error'
                        return
                    if event['status'] == 'submitted':
                        event['status'] = status


def list_inspections():
    with _LOCK:
        _expire()
        return [{key: item['snapshot'][key] for key in ('call_id', 'recipe', 'scenario', 'timestamp')}
                | {'provider_count': len(item['snapshot']['providers'])} for item in reversed(list(_BUILDS.values()))]


def get_inspection(call_id):
    with _LOCK:
        _expire()
        item = _BUILDS.get(call_id)
        return copy.deepcopy(item['snapshot']) if item else None


def clear_inspections():
    with _LOCK:
        _BUILDS.clear()
