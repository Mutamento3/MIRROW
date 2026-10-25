"""Explicit network opt-in for a text-free V3 shadow extraction report.

No extracted text, source ID, prompt, or provider payload is persisted here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from memory_v2.conversation_source import ConversationSource, is_canonical_session_id

from .day_material import batch_items, safe_plan, select_day_items
from .extraction import extract_batches
from .preview import historical_active_date
from .source_lifecycle import filter_current


async def run(dates: list[str], db_path: Path) -> list[dict]:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    from wander_manager.flash_structured import call_flash_json_detailed

    source = ConversationSource(db_path, canonical_sessions_only=True)
    reports = []
    for active_date in dates:
        if not historical_active_date(active_date):
            raise ValueError("pilot only accepts earlier YYYY-MM-DD active dates")
        rows = source.read_active_date(active_date, roles=("user", "assistant"))
        sessions = {row.session_id for row in rows if is_canonical_session_id(row.session_id)}
        if len(sessions) != 1:
            raise ValueError("pilot needs exactly one canonical session per active day")
        items = select_day_items(rows, session_id=next(iter(sessions)), active_date=active_date)
        batches = batch_items(items)
        usage_rows = []

        async def model(system: str, payload: str) -> dict:
            response = await call_flash_json_detailed(
                [{"role": "system", "content": system}, {"role": "user", "content": payload}],
                temperature=0.0, max_tokens=5000, enable_thinking=False,
                route_label="habit_radar_shadow",
            )
            if response.status != "ok" or not isinstance(response.parsed, dict):
                raise RuntimeError(f"habit shadow model failed: {response.status}/{response.error}")
            usage_rows.append({"provider": response.provider, "model": response.model,
                               "prompt_tokens": response.usage.get("prompt_tokens"),
                               "completion_tokens": response.usage.get("completion_tokens")})
            return response.parsed

        signals, evaluation = await extract_batches(batches, model)
        current, stale_reasons = filter_current(signals, source)
        reports.append({**safe_plan(active_date, items, batches),
                        "shadow_extraction": evaluation,
                        "source_validation": {"current": len(current),
                                              "stale_reasons": stale_reasons},
                        "usage": usage_rows})
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dates", nargs="+", help="historical stored active dates")
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).resolve().parents[1] / "events" / "event_chronicle.db")
    parser.add_argument("--live", action="store_true", help="confirm provider calls for this preview")
    args = parser.parse_args()
    if not args.live:
        parser.error("provider calls require --live")
    print(json.dumps(asyncio.run(run(args.dates, args.db)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
