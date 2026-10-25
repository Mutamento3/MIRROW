"""Read-only, narrowly whitelisted daily Sentinel statistics for the shadow pilot.

These are observed device aggregates, not claims about Human's habits or mood.
NULL means unknown. The summary has no sampling-coverage contract, so even a
non-NULL zero must not be interpreted as proof that an activity never happened.
"""

from __future__ import annotations

import sqlite3
import math
import json
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path


NUMERIC_FIELDS = (
    "steps", "sleep_min", "pc_active_min", "screen_time_total_min",
)
UNVERIFIED_FIELDS = ("screen_time_work_min", "screen_time_entertainment_min", "screen_time_social_min")


@dataclass(frozen=True)
class SentinelDayFacts:
    summary_date_key: str
    values: dict[str, int | float | None]
    updated_at: str | None
    coverage: str = "unknown"
    field_evidence: dict = field(default_factory=dict)

    def safe_report(self) -> dict:
        """Never expose private sensor values in pilot terminal reports."""
        return {"summary_date_key": self.summary_date_key, "summary_present": True,
                "stored_fields": [key for key, value in self.values.items() if value is not None],
                "missing_fields": [key for key, value in self.values.items() if value is None],
                "unverified_fields": list(UNVERIFIED_FIELDS),
                "date_alignment": "field_contracts_available" if self.field_evidence else "unverified_per_field",
                "fields_with_contracts": sorted(self.field_evidence),
                "coverage": self.coverage}


class SentinelDailySource:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()

    def list_dates(self, *, before, limit=30):
        if date.fromisoformat(before).isoformat() != before or not 1 <= limit <= 30:
            raise ValueError("invalid device evidence window")
        with closing(sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            return tuple(row[0] for row in conn.execute(
                "SELECT date FROM daily_health_summary WHERE date<? ORDER BY date DESC LIMIT ?",
                (before, limit)).fetchall())

    def read_date(self, calendar_date: str) -> SentinelDayFacts | None:
        if date.fromisoformat(calendar_date).isoformat() != calendar_date:
            raise ValueError("expected a calendar date in YYYY-MM-DD form")
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        with closing(sqlite3.connect(f"{self.db_path.as_uri()}?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row
            columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_health_summary)")}
            if "date" not in columns:
                raise ValueError("Sentinel daily_health_summary schema is unavailable")
            selected = [field for field in NUMERIC_FIELDS if field in columns]
            metadata = [key for key in ("updated_at", "field_evidence") if key in columns]
            fields = ["date", *selected, *metadata]
            row = conn.execute(
                f"SELECT {', '.join(fields)} FROM daily_health_summary WHERE date=?",
                (calendar_date,),
            ).fetchone()
        if row is None:
            return None
        values: dict[str, int | float | None] = {}
        for field in NUMERIC_FIELDS:
            value = row[field] if field in selected else None
            values[field] = (value if type(value) in (int, float) and math.isfinite(value)
                             and value >= 0 else None)
        return SentinelDayFacts(summary_date_key=calendar_date, values=values,
                                updated_at=row["updated_at"] if "updated_at" in metadata else None,
                                field_evidence=_read_evidence(row["field_evidence"], values, calendar_date)
                                if "field_evidence" in metadata else {})


def _read_evidence(raw, values, calendar_date):
    """Accept known producer contracts only; never treat row freshness as coverage."""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    allowed = {"pc_active_min": ("health_snapshots.input_idle_seconds", "observed_minute_bins", "sampled_minutes"),
               "screen_time_total_min": ("app_sessions.phone", "closed_interval_union", "collection_coverage_unknown")}
    accepted = {}
    for key, contract in parsed.items():
        if key not in allowed or not isinstance(contract, dict) or values.get(key) is None:
            continue
        source, method, coverage = allowed[key]
        next_date = (date.fromisoformat(calendar_date) + timedelta(days=1)).isoformat()
        expected_end = next_date + "T00:00:00+08:00"
        try:
            computed_at = datetime.fromisoformat(contract["computed_at"])
            if computed_at.tzinfo is None:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        base_keys = {"version", "source", "method", "date_basis", "timezone", "window_start",
                     "window_end", "computed_at", "day_closed", "value", "coverage_kind"}
        if key == "pc_active_min":
            extra_keys = {"sample_count", "observed_minutes", "day_minutes", "max_unobserved_minutes", "coverage_fraction"}
            integers = (contract.get("sample_count"), contract.get("observed_minutes"),
                        contract.get("max_unobserved_minutes"))
            if (any(type(item) is not int for item in integers)
                    or not 1 <= integers[1] <= 1440 or integers[0] < integers[1]
                    or not 0 <= integers[2] < 1440 or contract.get("day_minutes") != 1440
                    or not 0 <= values[key] <= integers[1]
                    or contract.get("coverage_fraction") != round(integers[1] / 1440, 6)):
                continue
        else:
            extra_keys = {"session_count", "interval_count", "recorded_seconds"}
            sessions, intervals = contract.get("session_count"), contract.get("interval_count")
            seconds = contract.get("recorded_seconds")
            if (type(sessions) is not int or type(intervals) is not int
                    or not 1 <= intervals <= sessions or type(seconds) not in (int, float)
                    or not math.isfinite(seconds) or not 0 < seconds <= 86400
                    or int(seconds // 60) != values[key]):
                continue
        if set(contract) != base_keys | extra_keys:
            continue
        if (type(contract.get("version")) is int and contract["version"] == 1
                and contract.get("source") == source and contract.get("method") == method
                and contract.get("coverage_kind") == coverage
                and contract.get("value") == values[key]
                and contract.get("date_basis") == "local_calendar_day"
                and contract.get("timezone") == "Asia/Shanghai"
                and contract.get("window_end") == expected_end
                and type(contract.get("day_closed")) is bool
                and contract["day_closed"] == (computed_at >= datetime.fromisoformat(expected_end))
                and contract.get("window_start") == calendar_date + "T00:00:00+08:00"):
            accepted[key] = contract
    return accepted


def safe_missing_report(calendar_date: str) -> dict:
    return {"summary_date_key": calendar_date, "summary_present": False,
            "stored_fields": [], "missing_fields": list(NUMERIC_FIELDS),
            "unverified_fields": list(UNVERIFIED_FIELDS),
            "fields_with_contracts": [],
            "date_alignment": "unverified_per_field", "coverage": "unknown"}
