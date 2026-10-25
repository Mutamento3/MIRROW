"""Shared validation of felt language, without guessing emotion semantics."""
import math
import json
import re
from typing import Any

from .sidecar import OPEN, CLOSE, parse_affect_sidecar

_LEGACY_MOOD_METRIC = re.compile(r"(?i)(?:^|[\s,;{])(?:t|l|s)\s*=\s*-?\d+(?:\.\d+)?")


def is_machine_state_label(value: Any) -> bool:
    text = str(value or '').strip()
    return len(_LEGACY_MOOD_METRIC.findall(text)) >= 2 or (
        text.startswith(('{', '[')) and text.endswith(('}', ']'))
    )


def natural_feeling(value: Any, cap: int = 120) -> str:
    if not isinstance(value, str) or is_machine_state_label(value):
        return ''
    return value.strip()[:cap]


def normalize_wander_feelings(raw: Any) -> dict | None:
    """Validate felt language and preserve the shared sidecar metadata."""
    if not isinstance(raw, dict):
        return None
    owner = raw.get('owner', 'k')
    if not isinstance(owner, str) or owner not in {'k', 'k_self'}:
        return None
    previous = raw.get('from', raw.get('from_emotion', '')) or ''
    feeling = raw.get('to', raw.get('to_emotion', ''))
    reason = raw.get('delta', '')
    if (not isinstance(previous, str) or is_machine_state_label(previous)
            or not natural_feeling(feeling) or not isinstance(reason, str) or not reason.strip()):
        return None
    try:
        confidence = float(raw.get('confidence', 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence)) if math.isfinite(confidence) else 0.0
    payload = {key: raw[key] for key in ('family', 'intensity', 'target', 'persistence') if key in raw}
    payload.update(operation='add', feeling=feeling, reason=reason)
    try:
        parsed = parse_affect_sidecar(OPEN + json.dumps(payload, ensure_ascii=False) + CLOSE)
    except (TypeError, ValueError):
        return None
    if not parsed.accepted:
        return None
    clean = parsed.payload
    return {'from_emotion': previous.strip(), 'to_emotion': clean['feeling'],
            'delta': clean['reason'], 'confidence': confidence,
            **{key: clean[key] for key in ('family', 'intensity', 'target', 'persistence')}}
