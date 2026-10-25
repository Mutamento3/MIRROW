"""Minimal, body-free ledger for final LLM prompts.

The existing context monitor is useful for human inspection, but a final
prompt audit needs a stable call identity and must not copy prompt bodies into
another log.  This module stores only message measurements and SHA-256
digests.  Provider usage is attached later when the HTTP boundary returns it.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


_LOCK = threading.RLock()
_RECORDS: Dict[str, Dict[str, Any]] = {}
_SIGNATURES: Dict[str, tuple] = {}
_COMMITTERS: Dict[str, list] = {}
_MAX_RECORDS = 500
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "prompt_ledger.jsonl")


def _text_measure(value: Any) -> tuple[int, str]:
    """Return (character estimate, digest input) without retaining content."""
    if isinstance(value, str):
        return len(value), value
    if isinstance(value, dict):
        if value.get("type") in {"image_url", "input_image", "image"}:
            return len("[image]"), "[image]"
        text = value.get("text") if isinstance(value.get("text"), str) else "[structured]"
        return len(text), text
    if isinstance(value, list):
        parts = []
        chars = 0
        for item in value:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = str(item.get("text") or "")
                    parts.append(text)
                    chars += len(text)
                elif item.get("type") in {"image_url", "input_image", "image"}:
                    # Never hash or serialise an image URL/base64 payload.
                    marker = "[image]"
                    parts.append(marker)
                    chars += len(marker)
            else:
                text = str(item)
                parts.append(text)
                chars += len(text)
        return chars, "".join(parts)
    text = str(value or "")
    return len(text), text


def _update_digest(hasher: Any, value: Any) -> None:
    """Hash a request value deterministically without serialising it to logs."""

    if value is None:
        hasher.update(b"N")
        return
    if isinstance(value, bool):
        hasher.update(b"B1" if value else b"B0")
        return
    if isinstance(value, (int, float)):
        data = repr(value).encode("ascii", errors="replace")
        hasher.update(b"D" + len(data).to_bytes(8, "big") + data)
        return
    if isinstance(value, str):
        data = value.encode("utf-8", errors="replace")
        hasher.update(b"S" + len(data).to_bytes(8, "big") + data)
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        hasher.update(b"Y" + len(data).to_bytes(8, "big") + data)
        return
    if isinstance(value, Mapping):
        hasher.update(b"M" + len(value).to_bytes(8, "big"))
        for key in sorted(value, key=lambda item: str(item)):
            _update_digest(hasher, str(key))
            _update_digest(hasher, value[key])
        return
    if isinstance(value, (list, tuple)):
        hasher.update(b"L" + len(value).to_bytes(8, "big"))
        for item in value:
            _update_digest(hasher, item)
        return
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            _update_digest(hasher, model_dump())
            return
        except Exception:
            pass
    _update_digest(hasher, str(value))


def _value_digest(value: Any) -> str:
    hasher = hashlib.sha256()
    _update_digest(hasher, value)
    return hasher.hexdigest()


def _provider_message_fields(message: Any) -> dict:
    """Return provider-relevant fields without Builder-private markers."""

    if isinstance(message, Mapping):
        return {
            str(key): value for key, value in message.items()
            if str(key) not in {"layer", "_section"} and not str(key).startswith("_")
        }
    result = {
        "role": getattr(message, "role", "unknown"),
        "content": getattr(message, "content", ""),
    }
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning is not None:
        result["reasoning_content"] = reasoning
    return result


def _estimate_tokens(chars: int, text: str) -> int:
    if not chars:
        return 0
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    return max(1, round(cjk / 1.5 + (chars - cjk) / 4))


def _message_stats(message: Any, index: int, layer: str = "") -> tuple[dict, tuple]:
    if isinstance(message, dict):
        role = str(message.get("role") or "unknown")
        content = message.get("content", "")
        layer = str(message.get("layer") or message.get("_section") or layer or "")
    else:
        role = str(getattr(message, "role", "unknown") or "unknown")
        content = getattr(message, "content", "")
    chars, digest_input = _text_measure(content)
    digest = hashlib.sha256(f"{role}\0{digest_input}".encode("utf-8", errors="replace")).hexdigest()
    resolved_layer = layer or ("output_contract" if role == "user" else "stable")
    stats = {
        "index": index,
        "role": role,
        "layer": resolved_layer,
        "chars": chars,
        "tokens": _estimate_tokens(chars, digest_input),
        "sha256": digest,
    }
    request_fields = _provider_message_fields(message)
    stats["request_fields"] = sorted(request_fields)
    stats["request_sha256"] = _value_digest(request_fields)
    if "reasoning_content" in request_fields:
        reasoning_chars, reasoning_text = _text_measure(request_fields.get("reasoning_content"))
        stats.update({
            "reasoning_chars": reasoning_chars,
            "reasoning_tokens": _estimate_tokens(reasoning_chars, reasoning_text),
            "reasoning_sha256": hashlib.sha256(
                reasoning_text.encode("utf-8", errors="replace")
            ).hexdigest(),
        })
    return stats, (role, chars, digest, resolved_layer)


def _provider_request_summary(
    messages: Sequence[Mapping[str, Any]], request_options: Optional[Mapping[str, Any]]
) -> dict:
    """Build a body-free signature of the exact message sequence and options."""

    safe_options = {}
    for key, value in (request_options or {}).items():
        key_text = str(key)
        lowered = key_text.casefold()
        if key_text == "messages" or any(
            part in lowered for part in ("key", "secret", "password", "authorization")
        ):
            continue
        safe_options[key_text] = value
    message_hashes = [str(item.get("request_sha256") or "") for item in messages]
    messages_sha256 = _value_digest(message_hashes)
    options_sha256 = _value_digest(safe_options)
    summary = {
        "sha256": _value_digest({
            "messages_sha256": messages_sha256,
            "options_sha256": options_sha256,
        }),
        "messages_sha256": messages_sha256,
        "options_sha256": options_sha256,
        "option_keys": sorted(safe_options),
    }
    for key in ("temperature", "max_tokens", "reasoning_effort"):
        value = safe_options.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
    extra_body = safe_options.get("extra_body")
    if isinstance(extra_body, Mapping):
        thinking = extra_body.get("thinking")
        if isinstance(thinking, Mapping) and isinstance(thinking.get("type"), str):
            summary["thinking_type"] = thinking["type"][:40]
    return summary


def _normalise_claim(claim: Any) -> Optional[dict]:
    if not isinstance(claim, dict):
        return None
    kind = str(claim.get("kind") or claim.get("resource") or "").strip()
    stable_id = claim.get("stable_id", claim.get("id"))
    if not kind or stable_id in (None, ""):
        return None
    result = {"kind": kind, "stable_id": stable_id}
    if isinstance(claim.get("ids"), (list, tuple, set)):
        result["ids"] = [str(x) for x in claim["ids"] if str(x)]
    # Keep only the bounded routing fields needed by a deferred commit.  Do
    # not copy session/message bodies into the audit/committer record.
    for key in (
        "recipient", "read_source", "read_source_id",
        "announce_source", "announce_source_id",
    ):
        value = claim.get(key)
        if value not in (None, ""):
            result[key] = str(value)[:200]
    if claim.get("store") is not None:
        result["store"] = claim["store"]
    return result


def _safe_meta(value: Any, max_chars: int = 120) -> Any:
    """Keep audit labels bounded and remove credential-looking keys."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in ("key", "token", "secret", "password", "authorization", "base64", "ip")):
                continue
            result[str(key)] = _safe_meta(item, max_chars)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_meta(item, max_chars) for item in list(value)[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)[:max_chars] if isinstance(value, str) else value
    return str(value)[:max_chars]


def _section_summaries(
    sections: Optional[Mapping[str, Any]], messages: Sequence[Any]
) -> list[dict]:
    """Return body-free per-section hashes for prefix churn diagnosis."""

    summaries: Dict[str, dict] = {}
    for raw_name, raw_value in (sections or {}).items():
        name = str(raw_name)
        value = raw_value if isinstance(raw_value, Mapping) else {}
        text = value.get("text", "")
        chars, digest_input = _text_measure(text)
        summaries[name] = {
            "name": name,
            "status": str(value.get("status") or ""),
            "placement": str(value.get("placement") or ""),
            "chars": chars,
            "tokens": _estimate_tokens(chars, digest_input),
            "message_count": 0,
            "sha256": hashlib.sha256(
                f"{name}\0{digest_input}".encode("utf-8", errors="replace")
            ).hexdigest(),
        }

    grouped: Dict[str, list[tuple[str, int, int, str]]] = {}
    for message in messages or []:
        if not isinstance(message, Mapping):
            continue
        name = str(message.get("_section") or "").strip()
        if not name:
            continue
        chars, digest_input = _text_measure(message.get("content", ""))
        role = str(message.get("role") or "unknown")
        digest = hashlib.sha256(
            f"{role}\0{digest_input}".encode("utf-8", errors="replace")
        ).hexdigest()
        grouped.setdefault(name, []).append(
            (role, chars, _estimate_tokens(chars, digest_input), digest)
        )

    for name, items in grouped.items():
        summary = summaries.setdefault(name, {
            "name": name, "status": "", "placement": "",
            "chars": 0, "tokens": 0, "message_count": 0, "sha256": "",
        })
        summary["chars"] = sum(item[1] for item in items)
        summary["tokens"] = sum(item[2] for item in items)
        summary["message_count"] = len(items)
        summary["sha256"] = hashlib.sha256(
            "\0".join(item[3] for item in items).encode("ascii")
        ).hexdigest()
    # Empty/gated sections cannot explain a serialized prompt change and
    # would multiply the append-only ledger size without diagnostic value.
    active = [
        summary for summary in summaries.values()
        if summary["chars"] > 0 or summary["message_count"] > 0
    ]
    for order, summary in enumerate(active, 1):
        summary["order"] = order
    return active


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex}"


def _identity_digest(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def register_prompt(
    messages: Sequence[Any],
    *,
    call_id: str = "",
    recipe: str = "",
    scenario: str = "",
    activity_id: str = "",
    session_id: str = "",
    layers: Optional[Sequence[str]] = None,
    one_shot_claims: Optional[Iterable[dict]] = None,
    provider: str = "",
    model: str = "",
    sections: Optional[Mapping[str, Any]] = None,
) -> str:
    """Register the final ordered prompt shape and return its ``call_id``."""
    call_id = call_id or new_call_id()
    ordered = []
    signature = []
    for index, message in enumerate(messages or [], 1):
        layer = layers[index - 1] if layers and index - 1 < len(layers) else ""
        stats, sig = _message_stats(message, index, layer)
        ordered.append(stats)
        signature.append(sig)
    layer_totals: Dict[str, Dict[str, int]] = {}
    for item in ordered:
        bucket = layer_totals.setdefault(item["layer"], {"messages": 0, "chars": 0, "tokens": 0})
        bucket["messages"] += 1
        bucket["chars"] += item["chars"]
        bucket["tokens"] += item["tokens"]
    claims = [normal for claim in (one_shot_claims or []) if (normal := _normalise_claim(claim))]
    record = {
        "call_id": call_id,
        "timestamp": datetime.now().isoformat(),
        "recipe": str(recipe or ""),
        "scenario": str(scenario or ""),
        "activity_id": str(activity_id or ""),
        # Session identity is useful for correlation but the raw local ID is
        # personal metadata and does not belong in a secondary audit log.
        "session_hash": _identity_digest(session_id),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "messages_count": len(ordered),
        "ordered_messages": ordered,
        "section_summaries": _section_summaries(sections, messages),
        "layer_totals": layer_totals,
        "status": "built",
        "provider_usage": {
            "available": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cache_hit_tokens": None,
            "cache_miss_tokens": None,
            "cost_yuan": None,
            "latency_ms": None,
        },
    }
    with _LOCK:
        _RECORDS[call_id] = record
        _SIGNATURES[call_id] = tuple(signature)
        _COMMITTERS[call_id] = claims
        while len(_RECORDS) > _MAX_RECORDS:
            old_id = next(iter(_RECORDS))
            _RECORDS.pop(old_id, None)
            _SIGNATURES.pop(old_id, None)
            _COMMITTERS.pop(old_id, None)
        _write_record(record)
    return call_id


_LOG_MAX_BYTES = 64 * 1024 * 1024  # 单文件上限，超过即轮转
_LOG_BACKUPS = 1                   # 保留的 .1 备份个数


def rotate_jsonl_if_needed(path: str, max_bytes: int = _LOG_MAX_BYTES,
                           backups: int = _LOG_BACKUPS) -> None:
    """按大小轮转 JSONL 审计文件（path → path.1 → … → path.N）。

    审计日志无限追加会拖垮磁盘（prompt_ledger 曾涨到 304MB）。轮转失败
    一律静默——审计永远不能影响聊天或主动送达这条主链路。
    """
    try:
        if os.path.getsize(path) < max_bytes:
            return
    except OSError:
        return
    try:
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(backups - 1, 0, -1):
            source = f"{path}.{index}"
            if os.path.exists(source):
                os.replace(source, f"{path}.{index + 1}")
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def _write_record(record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        rotate_jsonl_if_needed(_LOG_PATH)
        with open(_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Auditing must never break a chat or a proactive delivery.
        pass


def update_prompt_usage(
    call_id: str,
    usage: Any = None,
    *,
    model: str = "",
    provider: str = "",
    billing_provider: str = "",
    latency_ms: Optional[float] = None,
    status: str = "completed",
    messages: Optional[Sequence[Any]] = None,
    request_options: Optional[Mapping[str, Any]] = None,
    provider_request_id: str = "",
    provider_started_at: str = "",
    request_projection: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Attach real provider usage and the body-free final request shape."""
    if not call_id:
        return False
    from token_tracker import extract_token_usage
    values = extract_token_usage(usage)
    prompt, completion, total, cache_hit, cache_miss = (
        values[key] for key in ("prompt", "completion", "total", "cache_hit", "cache_miss")
    )
    cost = None
    billing_provider = billing_provider or (provider if provider in {"deepseek", "siliconflow"} else "")
    if model and billing_provider and all(value is not None for value in (cache_hit, cache_miss, completion)):
        try:
            from token_tracker import estimate_cost_yuan
            cost = estimate_cost_yuan(model, cache_hit, cache_miss, completion,
                                      datetime.now().isoformat(), provider=billing_provider)
        except Exception:
            cost = None
    provider_ordered = []
    provider_layer_totals: Dict[str, Dict[str, int]] = {}
    if messages is not None:
        for index, message in enumerate(messages or [], 1):
            # Provider serialization no longer carries Builder-private zone
            # markers.  Preserve the exact role/order/digest separately from
            # Builder's richer placement audit instead of guessing zones.
            stats, _ = _message_stats(message, index, "provider_sequence")
            provider_ordered.append(stats)
        for item in provider_ordered:
            bucket = provider_layer_totals.setdefault(
                item["layer"], {"messages": 0, "chars": 0, "tokens": 0}
            )
            bucket["messages"] += 1
            bucket["chars"] += item["chars"]
            bucket["tokens"] += item["tokens"]
    with _LOCK:
        record = _RECORDS.get(call_id)
        if record is None:
            return False
        record["provider"] = str(provider or record.get("provider") or "")
        record["billing_provider"] = billing_provider
        record["model"] = str(model or record.get("model") or "")
        record["status"] = status or "completed"
        # A Builder can feed multiple providers. These belong to this exact
        # submitted request and must never leak from the previous tool round.
        for field in ("provider_request_id", "provider_started_at", "provider_variant", "request_projection"):
            record.pop(field, None)
        if provider_request_id:
            record["provider_request_id"] = provider_request_id
        if provider_started_at:
            record["provider_started_at"] = provider_started_at
        record["provider_completed_at"] = datetime.now().astimezone().isoformat()
        if request_projection:
            record["provider_variant"] = str(request_projection.get("variant") or "")
            # Generated by the owned request adapter: measurements/digests only.
            record["request_projection"] = dict(request_projection)
        record["provider_usage"] = {
            "available": any(value is not None for value in (prompt, completion, total, cache_hit, cache_miss)),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cost_yuan": cost,
            "latency_ms": round(float(latency_ms), 1) if latency_ms is not None else None,
        }
        if messages is not None:
            record["provider_messages_count"] = len(provider_ordered)
            record["provider_ordered_messages"] = provider_ordered
            record["provider_layer_totals"] = provider_layer_totals
            record["provider_request"] = _provider_request_summary(
                provider_ordered, request_options
            )
        _write_record(record)
    return True


def _find_call_for_messages(messages: Sequence[Any]) -> Optional[str]:
    candidate = []
    for index, message in enumerate(messages or [], 1):
        stats, sig = _message_stats(message, index)
        candidate.append(sig)
    candidate_tuple = tuple(candidate)

    # Provider serializers intentionally strip Builder's private section/layer
    # metadata.  Matching therefore uses role/size/digest; layer remains an
    # audit field on the registered builder record, not an identity key.
    def comparable(signature: tuple) -> tuple:
        return tuple(item[:3] for item in signature)

    candidate_key = comparable(candidate_tuple)
    with _LOCK:
        # Prefer exact match, then a built prompt that is a prefix of a caller
        # which appended the actual user message/output contract afterwards.
        for call_id in reversed(list(_RECORDS)):
            signature = _SIGNATURES.get(call_id, ())
            signature_key = comparable(signature)
            if signature_key == candidate_key or (
                signature_key and len(signature_key) <= len(candidate_key)
                and candidate_key[:len(signature_key)] == signature_key
            ):
                if _RECORDS[call_id].get("status") in {"built", "provider_error"}:
                    return call_id
    return None


def record_provider_usage_for_messages(
    messages: Sequence[Any], usage: Any = None, *, model: str = "", provider: str = "",
    billing_provider: str = "",
    latency_ms: Optional[float] = None, status: str = "completed",
    request_options: Optional[Mapping[str, Any]] = None,
    source_messages: Optional[Sequence[Any]] = None,
    request_projection: Optional[Mapping[str, Any]] = None,
    provider_request_id: str = "", provider_started_at: str = "",
) -> Optional[str]:
    if source_messages is not None:
        # Explicit verified source mapping permits a provider-only role projection.
        if str((request_projection or {}).get("variant", "")).startswith("channel_"):
            from context_builder.channel_projection import verify_projection
        elif str((request_projection or {}).get("variant", "")).startswith("wander_"):
            from wander_manager.request_projection import verify_projection
        else:
            from behavior_scheduler.pro_request_adapter import verify_projection
        if not request_projection or not verify_projection(source_messages, messages, request_projection):
            return None
    call_id = _find_call_for_messages(source_messages if source_messages is not None else messages)
    if call_id:
        update_prompt_usage(
            call_id, usage, model=model, provider=provider, billing_provider=billing_provider,
            latency_ms=latency_ms, status=status, messages=messages,
            request_options=request_options,
            request_projection=request_projection, provider_request_id=provider_request_id,
            provider_started_at=provider_started_at,
        )
        # Social-feed reminder announcements are bound to the persisted
        # main-chat user turn.  The provider callback runs before the HTTP/WS
        # layer writes the assistant reply, so the caller's post-persistence
        # ``commit_context_result`` remains the only announcement commit point.
        with _LOCK:
            claims = list(_COMMITTERS.get(call_id, []))
        has_deferred_social = any(
            claim.get("kind") == "social_feed_announcements" for claim in claims
        )
        if status == "completed" and not has_deferred_social:
            commit_prompt_call(call_id)
    return call_id


def _commit_claim(claim: dict) -> None:
    kind = claim.get("kind")
    stable_id = claim.get("stable_id")
    ids = claim.get("ids") or []
    if kind == "prosody":
        from voice_manager.voice_cochlea import commit_prosody
        commit_prosody(str(stable_id))
    elif kind in {"sentinel_events", "sentinel_timeline"}:
        from silicon_perception.sentinel import get_sentinel
        sentinel = get_sentinel()
        annotator = getattr(sentinel, "_annotator", None) if sentinel else None
        if annotator:
            annotator.commit_timeline_annotations(ids or [stable_id])
    elif kind == "social_feed_announcements":
        # This is intentionally synchronous: commit_prompt_call runs inside
        # the HTTP/WS event loop and must not call asyncio.run.  The store
        # owns the BEGIN IMMEDIATE transaction and explicit-ID bound.
        store = claim.get("store")
        if store is None:
            from social_feed import get_social_feed_store
            store = get_social_feed_store()
        recipient = str(claim.get("recipient") or "k").strip().lower()
        if recipient != "k":
            return
        source = str(claim.get("announce_source") or "main_chat").strip().lower()
        source_id = str(claim.get("announce_source_id") or stable_id or "").strip()
        if source != "main_chat" or not source_id:
            return
        store.mark_notifications_announced_sync(
            "k", ids[:100], announce_source="main_chat", announce_source_id=source_id
        )


def commit_prompt_call(call_id: str) -> bool:
    """Commit one-shot resources at most once for a successful provider call."""
    if not call_id:
        return False
    with _LOCK:
        record = _RECORDS.get(call_id)
        if record is None or record.get("status") == "committed":
            return False
        claims = list(_COMMITTERS.get(call_id, []))
        if record.get("status") not in {"completed", "built"}:
            return False
        record["status"] = "committing"
    try:
        for claim in claims:
            _commit_claim(claim)
    except Exception:
        with _LOCK:
            if call_id in _RECORDS:
                _RECORDS[call_id]["status"] = "completed"
        return False
    with _LOCK:
        if call_id in _RECORDS:
            _RECORDS[call_id]["status"] = "committed"
            _write_record(_RECORDS[call_id])
    return True


def get_prompt_ledger(limit: int = 100) -> List[dict]:
    with _LOCK:
        values = list(_RECORDS.values())[-max(0, int(limit)):]
        return [json.loads(json.dumps(value, ensure_ascii=False)) for value in values]


def get_prompt_ledger_record(call_id: str) -> Optional[dict]:
    with _LOCK:
        value = _RECORDS.get(call_id)
        return json.loads(json.dumps(value, ensure_ascii=False)) if value else None


def clear_prompt_ledger() -> None:
    with _LOCK:
        _RECORDS.clear()
        _SIGNATURES.clear()
        _COMMITTERS.clear()


# Friendly aliases for callers/tests and for gradual integration.
record_prompt_ledger = register_prompt
start_prompt_call = register_prompt
finalize_prompt_usage = update_prompt_usage


__all__ = [
    "register_prompt", "record_prompt_ledger", "start_prompt_call",
    "update_prompt_usage", "finalize_prompt_usage",
    "record_provider_usage_for_messages", "commit_prompt_call",
    "get_prompt_ledger", "get_prompt_ledger_record", "clear_prompt_ledger",
    "new_call_id",
]
