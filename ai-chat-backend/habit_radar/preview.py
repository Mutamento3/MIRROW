"""Read-only, text-free source-size preview for completed active days."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_v2.conversation_source import ConversationSource, is_canonical_session_id

from .day_material import batch_items, safe_plan, select_day_items


def historical_active_date(value: str) -> bool:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return parsed.isoformat() == value and parsed < datetime.now(ZoneInfo("Asia/Shanghai")).date()


def preview_dates(db_path: str | Path, dates: list[str], *, session_id: str = "") -> list[dict]:
    source = ConversationSource(db_path, canonical_sessions_only=True)
    reports = []
    for active_date in dates:
        if not historical_active_date(active_date):
            raise ValueError("pilot only accepts earlier YYYY-MM-DD active dates")
        rows = source.read_active_date(active_date, roles=("user", "assistant"))
        sessions = {row.session_id for row in rows if is_canonical_session_id(row.session_id)}
        if session_id:
            if session_id not in sessions:
                raise ValueError("requested session has no material on this active day")
            chosen = session_id
        elif len(sessions) == 1:
            chosen = next(iter(sessions))
        else:
            raise ValueError("specify one canonical session for this active day")
        items = select_day_items(rows, session_id=chosen, active_date=active_date)
        reports.append(safe_plan(active_date, items, batch_items(items)))
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dates", nargs="+", help="stored active dates, YYYY-MM-DD")
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).resolve().parents[1] / "events" / "event_chronicle.db")
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()
    print(json.dumps(preview_dates(args.db, args.dates, session_id=args.session_id),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
