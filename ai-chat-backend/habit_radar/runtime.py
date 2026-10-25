"""Low-frequency main-loop scheduling; deliberately no chat/tick consumer."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from memory_v2.conversation_source import ConversationSource
from .service import run_day
from .store import ShadowStore

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "events" / "habit_radar_shadow.json"
DB_PATH = ROOT / "events" / "habit_radar_shadow.db"
AUTHORITY_PATH = ROOT / "events" / "event_chronicle.db"
logger = logging.getLogger(__name__)
_task = None
_cutoff_reader = None
_last_run = {"status": "not_started"}
_pending_closed_date = ""
_pending_closed_binding = ""


def closed_binding(active_date):
    from memory_v2.scene_closed_day import authority_binding
    return authority_binding(ConversationSource(AUTHORITY_PATH, canonical_sessions_only=True), active_date)


def current_task():
    """The caller can await this pass before consuming its new evidence."""
    return _task


def configuration():
    if not CONFIG_PATH.is_file():
        return None
    value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if value.get("enabled") is not True:
        return None
    start = value.get("start_active_date")
    if not isinstance(start, str) or date.fromisoformat(start).isoformat() != start:
        raise ValueError("invalid_shadow_start_date")
    return {"enabled": True, "start_active_date": start}


async def run_pass(cutoff, *, trigger, preferred_date=""):
    config = configuration()
    if not config:
        return {"status": "disabled"}
    source = ConversationSource(AUTHORITY_PATH, canonical_sessions_only=True)
    store = ShadowStore(DB_PATH, create=True)
    from llm_client import SILICONFLOW_STRUCTURED_MODEL
    dates = set(source.list_active_dates(before_active_date=cutoff, roles=("user",)))
    # Include now-empty days so deleting the last message invalidates its receipt.
    dates.update(d["active_date"] for d in store.days())
    results = []
    processed = 0
    for day in sorted((d for d in dates if config["start_active_date"] <= d < cutoff),
                      key=lambda d: (d != preferred_date, d)):
        result = await run_day(source, store, day, completed_before=cutoff,
                               model_identity=SILICONFLOW_STRUCTURED_MODEL)
        results.append(result)
        if result["status"] != "unchanged_or_deferred":
            processed += 1
        if processed >= 2:
            break
    return {"status": "error" if any(r["status"] == "error" for r in results) else "complete",
            "trigger": trigger, "completed_before": cutoff, "days": results}


async def _run(trigger, closed_active_date="", binding=""):
    global _last_run, _pending_closed_date, _pending_closed_binding
    try:
        while True:
            _last_run = {"status": "running", "trigger": trigger}
            # Only the validated day-end entry supplies this date. The shared
            # reader still checks every active topic before advancing cutoff.
            valid_close = closed_active_date and closed_binding(closed_active_date) == binding
            cutoff = (await _cutoff_reader(closed_active_date) if valid_close
                      else await _cutoff_reader())
            _last_run = await run_pass(cutoff, trigger=trigger,
                                       preferred_date=closed_active_date if valid_close else '')
            pending, _pending_closed_date = _pending_closed_date, ""
            pending_binding, _pending_closed_binding = _pending_closed_binding, ""
            if not pending:
                break
            closed_active_date, binding, trigger = pending, pending_binding, "topic_end"
        logger.info("Habit radar shadow: status=%s days=%s", _last_run["status"],
                    len(_last_run.get("days", [])))
    except asyncio.CancelledError:
        _last_run = {"status": "cancelled", "trigger": trigger}
        raise
    except Exception:
        # Exception payloads may carry private provider material or file paths.
        _last_run = {"status": "error", "error_code": "shadow_runtime_failed", "trigger": trigger}
        logger.warning("Habit radar shadow failed; inspect private shadow status")


def request_run(trigger="topic_end", *, closed_active_date=""):
    global _task, _pending_closed_date, _pending_closed_binding
    if closed_active_date:
        if trigger != "topic_end" or date.fromisoformat(closed_active_date).isoformat() != closed_active_date:
            raise ValueError("invalid_explicit_closed_date")
    if _cutoff_reader is None:
        return False
    binding = closed_binding(closed_active_date) if closed_active_date else ''
    if _task and not _task.done():
        if closed_active_date and closed_active_date >= _pending_closed_date:
            _pending_closed_date, _pending_closed_binding = closed_active_date, binding
        return bool(closed_active_date)
    try:
        if not configuration():
            return False
    except Exception:
        logger.warning("Habit radar shadow configuration invalid")
        return False
    _task = asyncio.create_task(_run(trigger, closed_active_date, binding), name="habit_radar_shadow")
    return True


def install(scheduler, loop, cutoff_reader):
    global _cutoff_reader
    _cutoff_reader = cutoff_reader
    if not configuration():
        return
    from apscheduler.triggers.cron import CronTrigger
    # Three bounded opportunities per day; no per-tick model analysis.
    scheduler.add_job(lambda: loop.call_soon_threadsafe(request_run, "scheduled"),
                      CronTrigger(hour="6,12,20", minute=20, timezone="Asia/Shanghai"),
                      id="habit_radar_shadow", replace_existing=True, coalesce=True,
                      misfire_grace_time=3600)
    request_run("startup")


async def shutdown():
    global _cutoff_reader, _pending_closed_date, _pending_closed_binding
    _cutoff_reader = None
    _pending_closed_date = ""
    _pending_closed_binding = ""
    if _task and not _task.done():
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)


def runtime_status():
    return {"configuration": configuration() or {"enabled": False},
            "phase": "shadow_only", "running": bool(_task and not _task.done()),
            "last_run": _last_run}
