"""Asynchronous closed-box digestion using the existing Memory V2 encoder."""
from __future__ import annotations

import asyncio
import logging
import time

from .conversation_source import ConversationSource
from .scene_digest_store import SceneDigestStore, cache_path
from .scene_planner import SceneReplayPlanner, scene_start_date

logger = logging.getLogger(__name__)
_loop = None
_task = None
_watcher = None
_pending = set()
_cutoff_reader = None
_last_report = {"status": "not_started"}
_recovery_started = False
_retry_dates = set()
_closed_dates = {}
_close_tasks = set()


async def _completed_before_day(day='', *, source=None, config=None, verify_formal=True):
    cutoff = await _cutoff_reader() if _cutoff_reader else ''
    if not day or not cutoff or day < cutoff:
        return cutoff
    if not verify_formal and day not in _closed_dates:
        return cutoff
    from .scene_closed_day import authority_binding, settled_binding
    if config is None:
        from .online_ingest import configuration_from_environment
        config = configuration_from_environment()
    if config is None:
        return cutoff
    source = source or ConversationSource(config.authority_db_path, canonical_sessions_only=True)
    current = await asyncio.to_thread(authority_binding, source, day)
    if day in _closed_dates and _closed_dates[day] != current:
        _closed_dates.pop(day, None)
        return cutoff
    if day not in _closed_dates:
        if not verify_formal:
            return cutoff
        try:
            proof = await asyncio.to_thread(settled_binding, source, config, day)
        except Exception:
            logger.warning('Scene formal closure could not be verified; conservative boundary retained')
            return cutoff
        if proof != current:
            return cutoff
        _closed_dates[day] = proof
    # The main reader still applies every active topic's lower boundary.
    return await _cutoff_reader(day)


async def _remember_closed(active_date):
    try:
        from .online_ingest import configuration_from_environment
        from .scene_closed_day import authority_binding
        config = configuration_from_environment()
        if config is None:
            return
        source = ConversationSource(config.authority_db_path, canonical_sessions_only=True)
        _closed_dates[active_date] = await asyncio.to_thread(authority_binding, source, active_date)
        _enqueue(active_date)
    except Exception:
        logger.warning('Scene closed-day evidence unavailable; conservative boundary retained')


def notify_closed(active_date):
    """Called only after the day-end entry has validated an explicit close."""
    from datetime import date
    try:
        date.fromisoformat(active_date)
    except (TypeError, ValueError):
        return False
    if _loop is None or _loop.is_closed():
        return False
    def schedule():
        task = asyncio.create_task(_remember_closed(active_date))
        _close_tasks.add(task)
        task.add_done_callback(_close_tasks.discard)
    _loop.call_soon_threadsafe(schedule)
    return True


def recovery_dates(source, start):
    """Scan metadata since the explicit rollout date; no historical backfill."""
    return tuple(day for day in source.list_active_dates() if day >= start)


async def digest_day(source, cache, active_date, *, completed_before="", encoder=None,
                     max_batches=2):
    from scene_manager.scene import configured_gap_minutes
    from .experiment import _encode_plan
    planner = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes())
    boxes = planner.boxes_for_day(active_date, completed_before=completed_before)
    encode = encoder or _encode_plan
    report = {"status": "complete", "encoded_batches": 0, "reused_batches": 0, "deferred_batches": 0,
              "request_count": 0, "prompt_tokens": 0, "completion_tokens": 0}
    previous_refs = ()
    previous_summaries = []
    previous_session = None
    for box in boxes:
        if box.session_id != previous_session:
            previous_refs, previous_summaries = (), []
            previous_session = box.session_id
        if not box.closed:
            continue
        for plan in box.batches:
            cached = cache.load(plan, source)
            if cached is not None:
                report["reused_batches"] += 1
                previous_summaries = [event.summary for event in cached.events][-3:]
                previous_refs = tuple(dict.fromkeys((*previous_refs, *plan.batch_sources)))
                continue
            if report["encoded_batches"] >= max_batches:
                report["status"] = "pending"
                return report
            if not cache.claim(plan, now=time.time(), context_sources=previous_refs):
                report["status"] = "deferred"
                report['deferred_batches'] += 1
                # A cooled-down encoding is not a missing source. Keep only
                # its actual raw boundary as context for later independent work.
                previous_summaries = []
                previous_refs = tuple(dict.fromkeys((*previous_refs, *plan.batch_sources)))
                continue
            # Context is bounded by whole messages. Every body supplied here
            # has a validated manifest and is ineligible as a new event source.
            context_messages = []
            from .replay import estimate_tokens
            for ref in reversed(previous_refs[-2:]):
                rows = source.read_message_ids((ref.message_id,))
                message = rows[0] if rows else None
                if message is not None and estimate_tokens(message.content) <= 1200:
                    context_messages.insert(0, {"timestamp": message.timestamp,
                                               "role": message.role, "content": message.content})
            context = {"summaries": previous_summaries, "edge_messages": context_messages}
            call_report, parsed = await encode(
                source, plan, {"active_date": active_date, "batch_id": plan.batch.batch_id},
                max_requests=2, preceding_context=context,
            )
            report["encoded_batches"] += 1
            report["request_count"] += int(call_report.get("request_count") or 0)
            usage = call_report.get("usage") or {}
            for field in ("prompt_tokens", "completion_tokens"):
                report[field] += int(usage.get(field) or 0)
            if parsed is None:
                report["status"] = "error"
                return report
            # A closing message can be withdrawn while the model is working.
            current = planner.boxes_for_day(active_date, completed_before=completed_before)
            valid = any(candidate.closed and candidate.box_id == box.box_id and
                        plan in candidate.batches for candidate in current)
            if not valid:
                report["status"] = "source_changed"
                return report
            cache.save(box, plan, parsed, call_report, source,
                       context_sources=previous_refs)
            previous_summaries = [event.summary for event in parsed.events][-3:]
            previous_refs = tuple(dict.fromkeys((*previous_refs, *plan.batch_sources)))
    return report


async def _drain():
    global _last_report
    try:
        from .online_ingest import configuration_from_environment
        config = configuration_from_environment()
        if config is None:
            return
        source = ConversationSource(config.authority_db_path, canonical_sessions_only=True)
        cache = SceneDigestStore(cache_path(source), create=True)
        while _pending:
            day = min(_pending)
            _pending.discard(day)
            cutoff = await _completed_before_day(day, source=source, config=config)
            from .online_ingest import _online_lock
            try:
                async with _online_lock:
                    _last_report = await digest_day(source, cache, day, completed_before=cutoff)
                    if _last_report['status'] == 'complete' and cutoff and day < cutoff:
                        from .scene_reconstruction import reconstruct_day
                        reconstruction = await reconstruct_day(source, day, completed_before=cutoff,
                                                               cache=cache, max_windows=2)
                        _last_report['reconstruction'] = reconstruction
                        for field in ('request_count', 'prompt_tokens', 'completion_tokens'):
                            _last_report[field] = _last_report.get(field, 0) + reconstruction.get(field, 0)
                        if reconstruction['status'] not in {'complete', 'cached', 'skipped'}:
                            _last_report['status'] = reconstruction['status']
            except asyncio.CancelledError:
                raise
            except Exception:
                _last_report = {"status": "error", "error_code": "scene_day_failed",
                                "encoded_batches": 0, "reused_batches": 0}
            logger.info("Scene digestion status=%s encoded=%s reused=%s",
                        _last_report["status"], _last_report["encoded_batches"],
                        _last_report["reused_batches"])
            if _last_report["status"] == "pending":
                _pending.add(day)
                await asyncio.sleep(0)
            elif _last_report["status"] == "complete":
                _retry_dates.discard(day)
            else:
                _retry_dates.add(day)
    except asyncio.CancelledError:
        raise
    except Exception:
        _last_report = {"status": "error", "error_code": "scene_digest_failed"}
        logger.warning("Scene digestion unavailable; raw context retained")


def _enqueue(active_date):
    global _task
    _pending.add(active_date)
    if _task is None or _task.done():
        _task = asyncio.create_task(_drain(), name="memory_v2_scene_digest")


def notify(active_date):
    """A notification only queues work; it never waits for the model in chat."""
    if _loop is None or _loop.is_closed():
        return False
    start = scene_start_date()
    if not start or not active_date or active_date < start:
        return False
    _loop.call_soon_threadsafe(_enqueue, active_date)
    return True


def install(loop, cutoff_reader):
    global _loop, _cutoff_reader
    _loop, _cutoff_reader = loop, cutoff_reader
    loop.call_soon_threadsafe(_start_recovery)


def _start_recovery():
    global _watcher
    if _watcher is None or _watcher.done():
        _watcher = asyncio.create_task(_recover(), name="memory_v2_scene_recovery")


async def recover_once():
    global _recovery_started
    from .online_ingest import configuration_from_environment
    start = scene_start_date()
    config = configuration_from_environment() if start else None
    if config is None:
        return 0
    source = ConversationSource(config.authority_db_path, canonical_sessions_only=True)
    dates = recovery_dates(source, start)
    if _recovery_started:
        dates = tuple(sorted(set(dates[-2:]) | _retry_dates))
    _recovery_started = True
    for day in dates:
        _enqueue(day)
    return len(dates)


async def completed_before(active_date=''):
    # Formal day assembly belongs to the background worker, never a foreground
    # context/inspection read. Warm proofs still validate their raw-day binding.
    return await _completed_before_day(active_date, verify_formal=False)


async def _recover():
    while _loop is not None:
        try:
            await recover_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Scene recovery unavailable; raw source remains available")
        await asyncio.sleep(60)


async def shutdown():
    global _loop, _cutoff_reader, _watcher, _task, _recovery_started
    _loop = _cutoff_reader = None
    active = [task for task in (_task, _watcher, *_close_tasks) if task and not task.done()]
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _task = _watcher = None
    _recovery_started = False
    _retry_dates.clear()
    _closed_dates.clear()
    _close_tasks.clear()
    _pending.clear()


def runtime_status():
    return {"enabled": bool(scene_start_date()), "last_report": dict(_last_report),
            "running": bool(_task and not _task.done()),
            "recovery_running": bool(_watcher and not _watcher.done())}
