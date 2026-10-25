"""Read-only monitor projections for Silicon Sentinel.

This module explains the observation boundary; it does not change source
health, freshness rules used by consumers, user state, or Flash decisions.
"""

from datetime import datetime
from typing import Any, Dict, Mapping, Optional


SOURCE_FIELD_KEYS: Dict[str, str] = {
    "heart_rate": "heart_rate",
    "screen_activity": "screen_activity",
    "input_idle": "input_idle",
    "mirrow_visibility": "mirrow_visibility",
    "user_status": "user_status",
    "period": "period",
    "step_count": "steps",
    "wifi_state": "wifi",
    "gps_location": "gps",
    "screen_time": "screen_time",
    "current_app": "current_app",
    "mobile_vision": "mobile_vision",
    "notifications": "notifications",
    "running_processes": "running_processes",
    "remote_session": "remote_session",
    "sensor_context": "sensor",
    "weather": "weather",
}


def _parse_local(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return None


def build_source_freshness_projection(
    snapshot,
    contracts: Mapping[str, Mapping[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    """Project field timestamps into monitor-friendly freshness states.

    Connection state answers whether a channel is currently usable. This
    projection separately answers how old the last confirmed fact is.
    """
    now = now or datetime.now()
    field_ts = getattr(snapshot, "field_ts", {}) if snapshot is not None else {}
    result: Dict[str, Dict[str, Any]] = {}

    for source_name, raw_contract in contracts.items():
        contract = dict(raw_contract or {})
        field_key = SOURCE_FIELD_KEYS.get(source_name, source_name)
        captured_at = (field_ts or {}).get(field_key)
        captured = _parse_local(captured_at)
        max_age = contract.get("nominal_max_age_seconds")

        if captured is None:
            age_seconds = None
            state = "unknown"
        else:
            age_seconds = max(0.0, (now - captured).total_seconds())
            if max_age is None:
                state = "persistent"
            else:
                state = "fresh" if age_seconds <= float(max_age) else "stale"

        result[source_name] = {
            "state": state,
            "captured_at": captured_at,
            "age_seconds": None if age_seconds is None else round(age_seconds, 1),
            "max_age_seconds": max_age,
            "field_key": field_key,
        }
    return result
