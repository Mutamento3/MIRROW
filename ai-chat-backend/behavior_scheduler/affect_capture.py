"""Request-local Affect control metadata, isolated from visible/tool channels."""
from contextvars import ContextVar
import hashlib
import json
import re
from typing import Any

from affect.sidecar import parse_affect_sidecar

_AFFECT_SIDECAR_CONTEXT: ContextVar[list[dict[str, Any]]] = ContextVar(
    'mirrow_affect_sidecars', default=[]
)
_AFFECT_PROVIDER_ATTEMPT: ContextVar[int] = ContextVar('mirrow_affect_attempt', default=0)
_AFFECT_PROVIDER_OK: ContextVar[bool] = ContextVar('mirrow_affect_provider_ok', default=False)


def begin_provider_attempt() -> None:
    """Each real call, including a retry, owns a distinct final candidate."""
    _AFFECT_PROVIDER_ATTEMPT.set(_AFFECT_PROVIDER_ATTEMPT.get() + 1)
    _AFFECT_PROVIDER_OK.set(False)


def _affect_visible_hash(text: Any) -> str:
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def has_tool_protocol(text: Any) -> bool:
    """Check control syntax, never infer human intent from keywords."""
    clean = str(text or '')
    if re.search(r'(?:TOOL_CALL\s*[:：]|\[/?TOOL_CALL\])', clean, re.IGNORECASE):
        return True
    if re.search(r'(?m)^\s*[@＠][A-Za-z][A-Za-z0-9_-]*(?:\s+.*?)?[@＠]\s*$', clean):
        return True
    try:
        obj = json.loads(clean.strip())
    except (TypeError, ValueError):
        return False
    return isinstance(obj, dict) and bool(obj.get('tool')) and isinstance(obj.get('parameters'), dict)


def _capture_affect_sidecar(text: Any, *, origin: str = 'stream', message_id: str = '',
                            eligible: bool | None = None) -> str:
    parsed = parse_affect_sidecar(str(text or ''))
    if parsed.found:
        entry = {
            'payload': parsed.payload, 'valid': parsed.valid,
            'eligible': not has_tool_protocol(parsed.clean_content) and eligible is not False,
            'diagnostics': list(parsed.diagnostics), 'origin': origin,
            'message_id': str(message_id or ''), 'attempt': _AFFECT_PROVIDER_ATTEMPT.get(),
        }
        current = list(_AFFECT_SIDECAR_CONTEXT.get() or [])
        # A visible hash is added later; it must not break duplicate detection.
        signature = lambda value: json.dumps({key: item for key, item in value.items() if key != 'visible_hash'},
                                            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if signature(entry) not in {signature(item) for item in current}:
            _AFFECT_SIDECAR_CONTEXT.set(current + [entry])
    return parsed.clean_content


def _strip_affect_sidecar_text(text: Any) -> str:
    """Derived channels are stripped, never recaptured as independent reports."""
    return parse_affect_sidecar(str(text or '')).clean_content


def _mark_latest_affect_visible_text(text: Any) -> None:
    current = list(_AFFECT_SIDECAR_CONTEXT.get() or [])
    attempt = _AFFECT_PROVIDER_ATTEMPT.get()
    for index in range(len(current) - 1, -1, -1):
        item = current[index]
        if item.get('attempt') == attempt and item.get('origin') in {'content', 'provider'} and not item.get('visible_hash'):
            current[index] = {**item, 'visible_hash': _affect_visible_hash(text)}
            _AFFECT_SIDECAR_CONTEXT.set(current)
            return


def _sanitize_provider_result(result: Any) -> Any:
    if not isinstance(result, dict):
        _AFFECT_PROVIDER_OK.set(False)
        return result
    clean = dict(result)
    ok = not any(clean.get(key) for key in ('error', 'refusal', 'routing_failed'))
    _AFFECT_PROVIDER_OK.set(ok)
    vision = clean.get('vision_envelope')
    vision_calls = vision.get('tool_calls') if isinstance(vision, dict) else None
    eligible = ok and not bool(clean.get('tool_call') or clean.get('tool_calls')
                               or vision_calls or clean.get('action_directives'))
    raw = clean.get('content')
    if isinstance(vision, dict):
        copied = dict(vision)
        nl = copied.get('natural_language')
        if nl and ('⟦MIRROW:AFFECT⟧' in str(nl) or '⟦/MIRROW:AFFECT⟧' in str(nl)):
            copied['natural_language'] = _capture_affect_sidecar(nl, origin='provider', eligible=eligible)
        else:
            if raw:
                _capture_affect_sidecar(raw, origin='provider', eligible=eligible)
            copied['natural_language'] = _strip_affect_sidecar_text(nl)
        clean['vision_envelope'] = copied
        clean['natural_language'] = copied['natural_language']
        clean['content'] = json.dumps(copied, ensure_ascii=False)
    else:
        if raw:
            clean['content'] = _capture_affect_sidecar(raw, origin='provider', eligible=eligible)
        if clean.get('natural_language'):
            clean['natural_language'] = (_strip_affect_sidecar_text(clean['natural_language']) if raw
                                        else _capture_affect_sidecar(clean['natural_language'], origin='provider', eligible=eligible))
    for field in ('reasoning', 'error', 'refusal'):
        if isinstance(clean.get(field), str):
            clean[field] = _strip_affect_sidecar_text(clean[field])
    return clean


def finalize_affect_sidecars(final_response: str, intermediate_messages: list,
                            existing: list, *, failed: bool = False) -> tuple[str, list]:
    attempt = _AFFECT_PROVIDER_ATTEMPT.get()
    final_allowed = not failed and (attempt == 0 or _AFFECT_PROVIDER_OK.get())
    final = _capture_affect_sidecar(final_response, origin='final', eligible=final_allowed)
    # Preserve raw protocol eligibility before discarding transport text.
    final_has_protocol = has_tool_protocol(final)
    from visible_reply import clean_visible_reply
    final = clean_visible_reply(final)
    for item in intermediate_messages or []:
        if isinstance(item, dict):
            if item.get('content'):
                item['content'] = _capture_affect_sidecar(item['content'], origin='intermediate',
                                                        message_id=str(item.get('id') or ''), eligible=False)
                item['content'] = clean_visible_reply(item['content'])
            if item.get('thinking'):
                item['thinking'] = _strip_affect_sidecar_text(item['thinking'])
    captured = list(_AFFECT_SIDECAR_CONTEXT.get() or [])
    if final.strip() and final_allowed and not final_has_protocol:
        final_hash = _affect_visible_hash(final)
        promote = next((i for i in range(len(captured) - 1, -1, -1)
                        if captured[i].get('attempt') == attempt
                        and captured[i].get('origin') in {'provider', 'content'}
                        and captured[i].get('valid') and captured[i].get('eligible')
                        and captured[i].get('visible_hash') == final_hash), -1)
        if promote >= 0:
            captured[promote] = {**captured[promote], 'origin': 'final'}
    merged = []
    seen = set()
    for item in list(existing or []) + captured:
        item = dict(item)
        if item.get('origin') == 'final' and (not final_allowed or not final.strip()
                                            or not item.get('eligible', True)
                                            or item.get('attempt', attempt) != attempt):
            item['origin'] = 'rejected'
        fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if fingerprint not in seen:
            merged.append(item)
            seen.add(fingerprint)
    return final, merged
