"""Body-free provider usage normalization shared by audit and token ledgers."""
from __future__ import annotations
import math
from typing import Any


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    result = getattr(value, name, None)
    extra = getattr(value, 'model_extra', None)
    return extra.get(name) if result is None and isinstance(extra, dict) else result


def _count(value: Any, *names: str) -> int | None:
    for name in names:
        raw = _field(value, name)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            number = int(raw)
            if number >= 0 and (not isinstance(raw, float) or math.isfinite(raw) and raw == number):
                return number
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def extract_token_usage(usage: Any) -> dict[str, int | None]:
    """Retain unknown counts, known zero, and each protocol's input semantics.

    Native Anthropic input_tokens excludes cache reads/writes; OpenAI prompt
    counts already include them. Gemini candidate counts exclude thought tokens.
    """
    billing = _field(usage, 'billing_usage')
    native = _field(billing, 'claude_usage')
    if _field(billing, 'semantic') == 'anthropic' and isinstance(native, dict):
        values = extract_token_usage(native)
        if values['reasoning'] is None:
            values['reasoning'] = _count(_field(usage, 'completion_tokens_details'), 'reasoning_tokens')
        return values
    prompt = _count(usage, 'prompt_tokens', 'input_tokens', 'promptTokenCount')
    completion = _count(usage, 'completion_tokens', 'output_tokens', 'completion')
    total = _count(usage, 'total_tokens', 'total', 'totalTokenCount')
    thought = _count(usage, 'thoughtsTokenCount')
    if completion is None:
        candidate = _count(usage, 'candidatesTokenCount')
        completion = candidate + thought if candidate is not None and thought is not None else candidate
    hit = _count(usage, 'prompt_cache_hit_tokens', 'cache_hit_tokens', 'cached_input_tokens',
                 'cached_tokens', 'cache_tokens', 'cachedContentTokenCount', 'cache_read_input_tokens')
    for name in ('prompt_tokens_details', 'input_tokens_details'):
        if hit is None:
            hit = _count(_field(usage, name), 'cached_tokens')
    miss = _count(usage, 'prompt_cache_miss_tokens', 'cache_miss_tokens')
    write = _count(usage, 'cache_creation_input_tokens', 'cache_write_input_tokens')
    creation = _field(usage, 'cache_creation')
    write_5m = _count(usage, 'claude_cache_creation_5_m_tokens')
    write_1h = _count(usage, 'claude_cache_creation_1_h_tokens')
    if write_5m is None:
        write_5m = _count(creation, 'ephemeral_5m_input_tokens')
    if write_1h is None:
        write_1h = _count(creation, 'ephemeral_1h_input_tokens')
    if write is None and write_5m is not None and write_1h is not None:
        write = write_5m + write_1h
    native_anthropic = (_count(usage, 'prompt_tokens') is None
                        and any(_field(usage, key) is not None for key in
                                ('cache_read_input_tokens', 'cache_creation_input_tokens', 'cache_creation')))
    if native_anthropic:
        uncached = _count(usage, 'input_tokens')
        prompt = uncached + hit + write if None not in (uncached, hit, write) else None
        miss = uncached + write if None not in (uncached, write) else None
    if prompt is not None:
        if hit is not None and hit > prompt:
            hit = None
        if miss is not None and miss > prompt:
            miss = None
        if miss is None and hit is not None:
            miss = prompt - hit
    reasoning = _count(_field(usage, 'completion_tokens_details'), 'reasoning_tokens')
    if reasoning is None:
        reasoning = _count(_field(usage, 'output_tokens_details'), 'reasoning_tokens')
    if reasoning is None:
        reasoning = thought
    return {'prompt': prompt, 'completion': completion, 'total': total,
            'cache_hit': hit, 'cache_miss': miss, 'cache_write': write,
            'cache_write_5m': write_5m, 'cache_write_1h': write_1h,
            'reasoning': reasoning}
