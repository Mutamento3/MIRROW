from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time
from typing import Any


VALID_MODES = {"off", "smart", "on"}
DEFAULT_THRESHOLDS = {
    "idle_minutes": 10,
    "immediate_chars": 500,
    "timed_min_chars": 80,
    "timed_seconds": 120,
    "cooldown_seconds": 180,
}
# Retention (24h) and relevance are different clocks. Old audio remains in
# the short-term store but must not consume CPU or produce delayed contact.
MAX_ANALYSIS_LAG_SECONDS = 120
MAX_CONTACT_MATERIAL_AGE_SECONDS = 300
GAMING_THRESHOLDS = {
    "immediate_chars": 80,
    "timed_min_chars": 30,
    "timed_seconds": 60,
    "cooldown_seconds": 90,
}
THRESHOLD_LIMITS = {
    "idle_minutes": (1, 240),
    "immediate_chars": (20, 10000),
    "timed_min_chars": (1, 10000),
    "timed_seconds": (10, 3600),
    "cooldown_seconds": (10, 3600),
}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    mode: str
    behavior_mode: str
    work_protected: bool
    private_idle_seconds: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def configured_mode() -> str:
    try:
        from settings_manager import get_setting
        value = str(get_setting("ambient_listening_mode") or "smart")
    except Exception:
        value = "smart"
    return value if value in VALID_MODES else "smart"


def listening_thresholds(value: Any = None) -> dict[str, int]:
    if value is None:
        try:
            from settings_manager import get_setting
            value = get_setting("ambient_listening_thresholds") or {}
        except Exception:
            value = {}
    value = value if isinstance(value, dict) else {}
    result: dict[str, int] = {}
    for key, default in DEFAULT_THRESHOLDS.items():
        low, high = THRESHOLD_LIMITS[key]
        try:
            parsed = int(value.get(key, default))
        except (TypeError, ValueError):
            parsed = default
        result[key] = max(low, min(high, parsed))
    result["timed_min_chars"] = min(result["timed_min_chars"], result["immediate_chars"])
    return result


def effective_listening_thresholds() -> dict[str, int]:
    thresholds = listening_thresholds()
    try:
        from wander_manager.user_status import get_status_meta
        if get_status_meta().activity_category == "gaming":
            thresholds.update(GAMING_THRESHOLDS)
    except Exception:
        pass
    return thresholds


def resolved_schedule() -> dict[str, Any]:
    """Read the SQLite authority and recover missing schedule fields from its JSON mirror."""
    schedule: dict[str, Any] = {}
    try:
        from silicon_perception.recording.health_store import get_store
        schedule = dict(get_store().get_user_schedule() or {})
    except Exception:
        pass
    try:
        from settings_manager import get_setting
        cached = get_setting("user_schedule") or {}
        if isinstance(cached, dict):
            for key in ("work_start", "work_end", "lunch_start", "lunch_end"):
                if not schedule.get(key) and cached.get(key):
                    schedule[key] = cached[key]
    except Exception:
        pass
    return schedule


def _parse_hhmm(value: Any) -> clock_time | None:
    try:
        return datetime.strptime(str(value), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _within(now: clock_time, start: clock_time, end: clock_time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def _work_protection(now: datetime) -> tuple[bool, str]:
    """Fail closed when a workday schedule cannot be resolved."""
    try:
        from calendar_manager.database import is_rest_day_today
        if is_rest_day_today():
            return False, "calendar_rest_day"
    except Exception:
        return True, "calendar_unavailable"
    try:
        schedule = resolved_schedule()
        start = _parse_hhmm(schedule.get("work_start"))
        end = _parse_hhmm(schedule.get("work_end"))
        if start is None or end is None:
            return True, "work_schedule_unavailable"
        return _within(now.time(), start, end), "work_window"
    except Exception:
        return True, "work_schedule_unavailable"


def _privacy_window(now: datetime) -> bool:
    try:
        from settings_manager import get_setting
        windows = get_setting("ambient_listening_privacy_windows") or []
    except Exception:
        return True
    for item in windows if isinstance(windows, list) else []:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        start, end = _parse_hhmm(item.get("start")), _parse_hhmm(item.get("end"))
        if start and end and _within(now.time(), start, end):
            return True
    return False


def _idle_seconds(now: datetime) -> float | None:
    try:
        from shared_state import get_last_private_chat_time
        last = get_last_private_chat_time(resolve_from_store=True)
        if last is None:
            return None
        if last.tzinfo and now.tzinfo is None:
            last = last.replace(tzinfo=None)
        if now.tzinfo and last.tzinfo is None:
            now = now.replace(tzinfo=None)
        return max(0.0, (now - last).total_seconds())
    except Exception:
        return None


def _behavior_mode() -> str:
    try:
        from wander_manager.user_status import get_status_meta
        value = str(get_status_meta().behavior_mode or "")
        return value if value in {"away", "active", "rest"} else "unknown"
    except Exception:
        return "unknown"


def capture_decision(now: datetime | None = None) -> PolicyDecision:
    now = now or datetime.now()
    mode = configured_mode()
    behavior = _behavior_mode()
    idle = _idle_seconds(now)
    protected, work_reason = _work_protection(now)
    if mode == "off":
        return PolicyDecision(False, "mode_off", mode, behavior, protected, idle)
    if _privacy_window(now):
        return PolicyDecision(False, "privacy_window", mode, behavior, protected, idle)
    if protected:
        return PolicyDecision(False, work_reason, mode, behavior, True, idle)
    if mode == "on":
        return PolicyDecision(True, "mode_on", mode, behavior, False, idle)
    if behavior in {"away", "active"}:
        return PolicyDecision(True, f"smart_{behavior}", mode, behavior, False, idle)
    if behavior == "rest":
        if idle is not None and idle >= listening_thresholds()["idle_minutes"] * 60:
            return PolicyDecision(True, "smart_rest_idle", mode, behavior, False, idle)
        return PolicyDecision(False, "rest_waiting_idle_threshold", mode, behavior, False, idle)
    return PolicyDecision(False, "status_unavailable", mode, behavior, False, idle)


def contact_decision(now: datetime | None = None) -> PolicyDecision:
    now = now or datetime.now()
    base = capture_decision(now)
    if not base.allowed:
        return base
    if base.private_idle_seconds is None:
        return PolicyDecision(False, "private_chat_clock_unavailable", base.mode,
                              base.behavior_mode, base.work_protected, None)
    if base.private_idle_seconds < listening_thresholds()["idle_minutes"] * 60:
        return PolicyDecision(False, "contact_waiting_idle_threshold", base.mode,
                              base.behavior_mode, base.work_protected,
                              base.private_idle_seconds)
    return PolicyDecision(True, "contact_allowed", base.mode, base.behavior_mode,
                          base.work_protected, base.private_idle_seconds)
