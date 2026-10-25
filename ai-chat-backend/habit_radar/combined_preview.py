"""Read-only material presence preview; same date key is not proven alignment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memory_v2.conversation_source import ConversationSource, is_canonical_session_id

from .day_material import batch_items, safe_plan, select_day_items
from .preview import historical_active_date
from .sentinel_facts import SentinelDailySource, safe_missing_report


def preview_dates(conversation_db: str | Path, sentinel_db: str | Path,
                  dates: list[str]) -> list[dict]:
    conversation = ConversationSource(conversation_db, canonical_sessions_only=True)
    sentinel = SentinelDailySource(sentinel_db)
    reports = []
    for active_date in dates:
        if not historical_active_date(active_date):
            raise ValueError("pilot only accepts earlier YYYY-MM-DD active dates")
        rows = conversation.read_active_date(active_date, roles=("user", "assistant"))
        sessions = {row.session_id for row in rows if is_canonical_session_id(row.session_id)}
        if len(sessions) != 1:
            raise ValueError("pilot needs exactly one canonical session per active day")
        items = select_day_items(rows, session_id=next(iter(sessions)), active_date=active_date)
        calendar_dates = sorted({item.calendar_date for item in items})
        sensor_reports = []
        for calendar_date in calendar_dates:
            facts = sentinel.read_date(calendar_date)
            sensor_reports.append(facts.safe_report() if facts else safe_missing_report(calendar_date))
        reports.append({**safe_plan(active_date, items, batch_items(items)),
                        "source_calendar_dates": calendar_dates,
                        "sentinel_daily_availability": sensor_reports})
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dates", nargs="+", help="historical stored active dates")
    parser.add_argument("--conversation-db", type=Path,
                        default=Path(__file__).resolve().parents[1] / "events" / "event_chronicle.db")
    parser.add_argument("--sentinel-db", type=Path,
                        default=Path(__file__).resolve().parents[1] / "silicon_perception"
                        / "data" / "silicon_perception.db")
    args = parser.parse_args()
    print(json.dumps(preview_dates(args.conversation_db, args.sentinel_db, args.dates),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
