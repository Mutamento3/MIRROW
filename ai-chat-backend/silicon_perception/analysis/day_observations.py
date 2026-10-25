"""Deterministic day observations, shared by daily aggregation and read-only audits.

Naive timestamps follow MIRROW's local clock. A sample establishes one observed
minute, not continuous activity between samples. Phone intervals are recorded
app sessions, not proof that collection ran for the rest of the day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


LOCAL = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DayObservation:
    value: int
    evidence: dict


def local_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=LOCAL) if parsed.tzinfo is None else parsed.astimezone(LOCAL)
    except (TypeError, ValueError):
        return None


def day_bounds(target_date: str):
    start = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=LOCAL)
    if start.date().isoformat() != target_date:
        raise ValueError("expected YYYY-MM-DD")
    return start, start + timedelta(days=1)


def _base(target_date, source, method, now):
    start, end = day_bounds(target_date)
    return {"version": 1, "source": source, "method": method,
            "date_basis": "local_calendar_day", "timezone": "Asia/Shanghai",
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "computed_at": now.isoformat(), "day_closed": now >= end}


def pc_observation(connection, target_date: str, *, now=None) -> DayObservation | None:
    start, end = day_bounds(target_date)
    now = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(health_snapshots)")}
    if not {"timestamp", "input_idle_seconds", "input_origin_available"}.issubset(columns):
        return None
    rows = connection.execute(
        "SELECT timestamp, input_idle_seconds FROM health_snapshots "
        "WHERE substr(timestamp,1,10) BETWEEN ? AND ? AND input_origin_available=1",
        ((start - timedelta(days=1)).date().isoformat(), end.date().isoformat()),
    )
    observed, active = set(), set()
    sample_count = 0
    for raw_time, idle in rows:
        at = local_time(raw_time)
        if (at is None or not start <= at < end or at > now
                or type(idle) not in (int, float) or not math.isfinite(idle) or idle < 0):
            continue
        minute = int((at - start).total_seconds() // 60)
        observed.add(minute)
        sample_count += 1
        if idle < 300:
            active.add(minute)
    if not observed:
        return None
    ordered = sorted(observed)
    gaps = [ordered[0], 1439 - ordered[-1],
            *(later - earlier - 1 for earlier, later in zip(ordered, ordered[1:]))]
    evidence = _base(target_date, "health_snapshots.input_idle_seconds", "observed_minute_bins", now)
    evidence.update({"value": len(active), "sample_count": sample_count,
                     "observed_minutes": len(observed), "day_minutes": 1440,
                     "coverage_kind": "sampled_minutes", "max_unobserved_minutes": max(gaps),
                     "coverage_fraction": round(len(observed) / 1440, 6)})
    return DayObservation(len(active), evidence)


def phone_observation(connection, target_date: str, *, now=None) -> DayObservation | None:
    start, end = day_bounds(target_date)
    now = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(app_sessions)")}
    if not {"session_start", "session_end", "platform", "duration_seconds"}.issubset(columns):
        return None
    rows = connection.execute(
        "SELECT session_start, session_end, duration_seconds FROM app_sessions "
        "WHERE platform='phone' AND substr(session_start,1,10)<=? "
        "AND substr(session_end,1,10)>=?",
        (end.date().isoformat(), (start - timedelta(days=1)).date().isoformat()),
    )
    intervals = []
    for raw_start, raw_end, duration in rows:
        at, until = local_time(raw_start), local_time(raw_end)
        if (at is None or until is None or until <= at or until > now
                or type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0
                or abs((until - at).total_seconds() - duration) >= 2):
            continue
        left, right = max(start, at), min(end, until)
        if right > left:
            intervals.append((left, right))
    if not intervals:
        return None
    merged = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    seconds = sum((right - left).total_seconds() for left, right in merged)
    evidence = _base(target_date, "app_sessions.phone", "closed_interval_union", now)
    evidence.update({"value": int(seconds // 60), "session_count": len(intervals),
                     "interval_count": len(merged), "recorded_seconds": seconds,
                     "coverage_kind": "collection_coverage_unknown"})
    return DayObservation(int(seconds // 60), evidence)
