"""Isolated whole-day encoding followed by one recoverable day settlement."""

from __future__ import annotations

import tempfile
import logging
import sqlite3
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .conversation_source import ConversationSource
from .day_settlement import DAY_SETTLEMENT_VERSION
from .day_settlement_assembler import DaySettlementPlanAssembler
from .day_settlement_experiment import FlashDaySettlementModelAdapter
from .day_settlement_orchestrator import settle_completed_day
from .day_settlement_runner import day_settlement_prompt_version
from .encoding import ParsedEncoding, assess_encoding_quality
from .replay import ReplayPlanner
from .store import MemoryV2Store


def _completed_before(active_date: str) -> str:
    return (date.fromisoformat(active_date) + timedelta(days=1)).isoformat()


def _day_commit_valid(
    store: MemoryV2Store,
    source: ConversationSource,
    day: Any,
    parsed_batches: list[ParsedEncoding],
    settlement_report: dict[str, Any],
    *,
    completed_before_active_date: str | None = None,
    explicit_retry: bool = False,
) -> bool:
    """Verify this day's batches plus its one settlement/explicit zero-event skip."""

    if len(parsed_batches) != len(day.batches):
        return False
    for plan, parsed in zip(day.batches, parsed_batches):
        cached = store.load_completed_events(
            plan.batch,
            batch_sources=plan.batch_sources,
            batch_source_validator=source.validate_batch_sources,
        )
        if cached is None or len(cached) != len(parsed.events):
            return False
    if settlement_report.get("status") == "skipped":
        if settlement_report.get("skip_reason") != "no_active_events":
            return False
        assembly = DaySettlementPlanAssembler(store.db_path).assemble(
            active_date=day.active_date,
            completed_before_active_date=(
                completed_before_active_date or _completed_before(day.active_date)
            ),
        )
        return not assembly.ready and assembly.skip_reason == "no_active_events"
    if settlement_report.get("status") not in {"committed", "cached"}:
        return False
    if not settlement_report.get("receipt_persisted"):
        return False
    receipt = store.get_day_settlement_receipt_for_day(
        source_namespace="mainline",
        active_date=day.active_date,
        settlement_version=DAY_SETTLEMENT_VERSION,
        prompt_version=day_settlement_prompt_version(day.active_date),
    )
    return bool(
        receipt is not None
        and receipt["id"] == settlement_report.get("settlement_id")
        and receipt["source_digest"] == settlement_report.get("source_digest")
        and receipt["execution_digest"]
        == settlement_report.get("execution_digest")
    )


async def run_backfill_day(
    source: ConversationSource,
    planner: ReplayPlanner,
    active_date: str,
    *,
    max_requests: int = 1,
    work_db_path: str | Path | None = None,
    repair_quality: bool = False,
    completed_before_active_date: str | None = None,
    explicit_retry: bool = False,
    max_continuation_pages: int = 0,
) -> dict[str, Any]:
    """Encode each batch once, then settle the completed day at most once."""

    from .experiment import (
        _combined_usage,
        _commit_encoding,
        _encode_plan,
        _resolve_encoding_quality,
    )

    day = planner.plan_active_date(active_date)
    if type(max_continuation_pages) is not int or max_continuation_pages < 0:
        raise ValueError("max_continuation_pages_must_be_nonnegative")
    settlement_cutoff = completed_before_active_date or _completed_before(active_date)
    base = {
        "active_date": active_date,
        "source_message_count": day.message_count,
        "estimated_source_tokens": day.estimated_tokens,
        "batch_count": len(day.batches),
        "settlement_count": int(bool(day.batches)),
        "work_store_persistent": work_db_path is not None,
    }
    empty_settlement = {
        "status": "not_started",
        "model_call_count": 0,
        "receipt_persisted": False,
    }
    if not day.batches:
        return {
            **base,
            "status": "empty_day",
            "temporary_commit_valid": False,
            "request_count": 0,
            "encoding_request_count": 0,
            "settlement_request_count": 0,
            "usage": {},
            "encoding_reports": [],
            "settlement_report": empty_settlement,
        }

    encoding_reports: list[dict[str, Any]] = []
    parsed_batches: list[ParsedEncoding] = []
    with ExitStack() as stack:
        if work_db_path is None:
            temp_dir = stack.enter_context(tempfile.TemporaryDirectory())
            resolved_work_db = Path(temp_dir) / "memory_v2_day_experiment.db"
        else:
            resolved_work_db = Path(work_db_path)
        store = MemoryV2Store(resolved_work_db)
        for batch_index, plan in enumerate(day.batches, start=1):
            cached_events = store.load_completed_events(
                plan.batch,
                batch_sources=plan.batch_sources,
                batch_source_validator=source.validate_batch_sources,
            )
            if cached_events is None:
                from .scene_digest_store import SceneDigestStore, cache_path
                pending = SceneDigestStore(cache_path(source)).load(plan, source)
                if pending is not None:
                    _commit_encoding(store, source, plan, pending)
                    cached_events = pending.events
            if cached_events is not None:
                parsed = ParsedEncoding(events=cached_events)
                parsed_batches.append(parsed)
                cached_quality = assess_encoding_quality(parsed, plan).safe_dict()
                if repair_quality and cached_quality["review_required"]:
                    encoding_reports.append(
                        {
                            "active_date": active_date,
                            "batch_number": batch_index,
                            "batch_id": plan.batch.batch_id,
                            "source_message_count": plan.batch.source_count,
                            "estimated_source_tokens": plan.estimated_tokens,
                            "status": "quality_cache_conflict",
                            "error": "cached encoding lacks a clean quality resolution",
                            "request_count": 0,
                            "usage": {},
                            "event_count": len(cached_events),
                            "quality": cached_quality,
                        }
                    )
                    return {
                        **base,
                        "status": "batch_encoding_error",
                        "failed_batch_number": batch_index,
                        "error": "cached encoding lacks a clean quality resolution",
                        "temporary_commit_valid": False,
                        "request_count": 0,
                        "encoding_request_count": 0,
                        "settlement_request_count": 0,
                        "usage": {},
                        "encoding_reports": encoding_reports,
                        "settlement_report": empty_settlement,
                    }
                encoding_reports.append(
                    {
                        "active_date": active_date,
                        "batch_number": batch_index,
                        "batch_id": plan.batch.batch_id,
                        "source_message_count": plan.batch.source_count,
                        "estimated_source_tokens": plan.estimated_tokens,
                        "status": "cached",
                        "request_count": 0,
                        "usage": {},
                        "event_count": len(cached_events),
                        "quality": cached_quality,
                        "quality_resolution": {"status": "cached_clean"},
                    }
                )
                continue

            report, parsed = await _encode_plan(
                source,
                plan,
                {
                    "active_date": active_date,
                    "batch_number": batch_index,
                    "batch_id": plan.batch.batch_id,
                    "source_message_count": plan.batch.source_count,
                    "estimated_source_tokens": plan.estimated_tokens,
                },
                max_requests=max_requests,
            )
            if parsed is not None:
                report, parsed = await _resolve_encoding_quality(
                    source,
                    plan,
                    report,
                    parsed,
                    enabled=repair_quality,
                )
            encoding_reports.append(report)
            if parsed is None:
                encoding_requests = sum(
                    int(item.get("request_count", 0)) for item in encoding_reports
                )
                return {
                    **base,
                    "status": "batch_encoding_error",
                    "failed_batch_number": batch_index,
                    "error": report.get("error", "encoding_failed"),
                    "temporary_commit_valid": False,
                    "request_count": encoding_requests,
                    "encoding_request_count": encoding_requests,
                    "settlement_request_count": 0,
                    "usage": _combined_usage(
                        *(item.get("usage", {}) for item in encoding_reports)
                    ),
                    "encoding_reports": encoding_reports,
                    "settlement_report": empty_settlement,
                }
            _commit_encoding(store, source, plan, parsed)
            parsed_batches.append(parsed)

        encoding_requests = sum(
            int(item.get("request_count", 0)) for item in encoding_reports
        )
        encoding_usage = _combined_usage(
            *(item.get("usage", {}) for item in encoding_reports)
        )
        adapter = FlashDaySettlementModelAdapter()
        settlement = await settle_completed_day(
            store,
            active_date=active_date,
            completed_before_active_date=settlement_cutoff,
            model_call=adapter,
        )
        settlement_report = settlement.safe_dict()
        settlement_usage = adapter.safe_usage()
        settlement_requests = adapter.call_count
        combined_usage = _combined_usage(encoding_usage, settlement_usage)
        committed = _day_commit_valid(
            store,
            source,
            day,
            parsed_batches,
            settlement_report,
            completed_before_active_date=settlement_cutoff,
        )
        groups = getattr(settlement, "scene_groups", ())
        if committed and groups:
            from .scene_digest_store import SceneDigestStore, cache_path
            try:
                SceneDigestStore(cache_path(source), create=True).save_scene_groups(
                    active_date, settlement.settlement_id,
                    tuple(ref for batch in day.batches for ref in batch.batch_sources), groups, source)
            except (OSError, ValueError, sqlite3.Error) as exc:
                # Formal events have committed. A missing derived grouping must
                # fall back to valid atomic impressions, not fail that commit.
                logging.getLogger(__name__).warning("scene grouping cache unavailable: %s", type(exc).__name__)
        review_required_batch_count = sum(
            bool(item.get("quality", {}).get("review_required"))
            for item in encoding_reports
        )
        if not settlement.successful or not committed:
            return {
                **base,
                "status": (
                    f"day_settlement_{settlement.status}"
                    if not settlement.successful
                    else "day_settlement_commit_invalid"
                ),
                "error": settlement.error_code or "settlement_commit_not_verified",
                "temporary_commit_valid": False,
                "request_count": encoding_requests + settlement_requests,
                "encoding_request_count": encoding_requests,
                "settlement_request_count": settlement_requests,
                "usage": combined_usage,
                "event_count": sum(len(parsed.events) for parsed in parsed_batches),
                "quality_review_required": review_required_batch_count > 0,
                "encoding_reports": encoding_reports,
                "settlement_report": settlement_report,
            }

        # Separate receipt lane: cached day compacts must not swallow pending
        # edge work. Offline waves default to zero to honor their existing budget.
        continuation = {"status": "disabled", "model_call_count": 0}
        if max_continuation_pages and settlement.receipt_persisted:
            from .continuation_pages import run_continuation_pages
            page_adapter = FlashDaySettlementModelAdapter(usage_tag="memory_v2_continuation_pages")
            page_result = await run_continuation_pages(store, settlement_id=settlement.settlement_id,
                model_call=page_adapter, max_model_calls=max_continuation_pages, explicit_retry=explicit_retry)
            continuation = page_result.safe_dict()
            combined_usage = _combined_usage(combined_usage, page_adapter.safe_usage())
            if not page_result.successful:
                return {**base, "status": "continuation_" + page_result.status,
                        "temporary_commit_valid": True,
                        "request_count": encoding_requests + settlement_requests + page_result.model_call_count,
                        "settlement_request_count": settlement_requests, "encoding_request_count": encoding_requests,
                        "usage": combined_usage, "settlement_report": settlement_report,
                        "continuation_report": continuation}

    # Reconstruct independently of the immutable day receipt, including when
    # that receipt was cached. Missing derived views must remain recoverable.
    from .scene_reconstruction import reconstruct_day
    from .scene_planner import SceneReplayPlanner, scene_start_date
    reconstruction = {'status': 'skipped', 'request_count': 0}
    if scene_start_date() and active_date >= scene_start_date():
        from .scene_digest_store import SceneDigestStore, cache_path
        from scene_manager.scene import configured_gap_minutes
        cache = SceneDigestStore(cache_path(source), create=True)
        closed = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes()).boxes_for_day(
            active_date, completed_before=settlement_cutoff)
        encodings = {plan.batch.batch_id: parsed for plan, parsed in zip(day.batches, parsed_batches)}
        for box in closed:
            for plan in box.batches:
                parsed = encodings.get(plan.batch.batch_id)
                if parsed is not None and cache.load(plan, source) is None:
                    cache.save(box, plan, parsed, {'request_count': 0, 'reused_canonical': True}, source)
        reconstruction = await reconstruct_day(source, active_date,
                                               completed_before=settlement_cutoff, cache=cache,
                                               explicit_retry=explicit_retry)
        combined_usage = _combined_usage(combined_usage, reconstruction)
    reconstruction_ok = reconstruction['status'] in {'complete', 'cached', 'skipped'}
    return {
        **base,
        "status": "ok" if reconstruction_ok else "scene_reconstruction_" + reconstruction['status'],
        "contract_valid": True,
        "temporary_commit_valid": True,
        "request_count": encoding_requests + settlement_requests + reconstruction['request_count'] + continuation["model_call_count"],
        "continuation_report": continuation,
        "reconstruction_report": reconstruction,
        "encoding_request_count": encoding_requests,
        "settlement_request_count": settlement_requests,
        "usage": combined_usage,
        "event_count": sum(len(parsed.events) for parsed in parsed_batches),
        "review_required_batch_count": review_required_batch_count,
        "quality_review_required": review_required_batch_count > 0,
        "settlement_receipt_count": int(settlement.receipt_persisted),
        "day_compact_item_count": settlement.compact_item_count,
        "accepted_continuation_count": settlement.accepted_continuation_count,
        "encoding_reports": encoding_reports,
        "settlement_report": settlement_report,
    }


__all__ = ["_day_commit_valid", "run_backfill_day"]
