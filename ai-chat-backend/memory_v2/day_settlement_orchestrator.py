"""Recoverable orchestration for one completed active-day settlement."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .day_settlement import day_settlement_input_digest, resolve_day_settlement_id
from .day_settlement_assembler import (
    DaySettlementAssemblyError,
    DaySettlementPlanAssembler,
)
from .day_settlement_persistence import prepare_day_settlement_draft
from .day_settlement_runner import (
    DEFAULT_DAY_SETTLEMENT_MAX_OUTPUT_TOKENS,
    day_settlement_prompt_version,
    DaySettlementModelCall,
    day_settlement_execution_digest,
    run_day_settlement,
)
from .store import MemoryV2Store


_SETTLEMENT_LOCKS: dict[tuple[str, str, str, int], asyncio.Lock] = {}


@dataclass(frozen=True)
class DaySettlementOrchestrationResult:
    status: str
    active_date: str
    source_namespace: str
    source_digest: str = ""
    settlement_id: str = ""
    execution_digest: str = ""
    model_call_count: int = 0
    receipt_persisted: bool = False
    compact_item_count: int = 0
    candidate_count: int = 0
    judgment_count: int = 0
    accepted_continuation_count: int = 0
    skip_reason: str = ""
    error_code: str = ""
    normalization_codes: tuple[str, ...] = ()
    scene_groups: tuple[dict, ...] = ()
    deferred_candidate_count: int = 0
    candidate_inventory_recorded: bool = False
    remaining_in_budget_count: int | None = None

    @property
    def successful(self) -> bool:
        return self.status in {"committed", "cached", "skipped"}

    def safe_dict(self) -> dict[str, Any]:
        unjudged = (self.remaining_in_budget_count if self.remaining_in_budget_count is not None
                    else max(0, self.candidate_count - self.judgment_count))
        return {
            "status": self.status,
            "active_date": self.active_date,
            "source_namespace": self.source_namespace,
            "source_digest": self.source_digest,
            "settlement_id": self.settlement_id,
            "execution_digest": self.execution_digest,
            "model_call_count": self.model_call_count,
            "receipt_persisted": self.receipt_persisted,
            "compact_item_count": self.compact_item_count,
            "candidate_count": self.candidate_count,
            "judgment_count": self.judgment_count,
            "unjudged_candidate_count": unjudged,
            "remaining_candidate_count": unjudged + self.deferred_candidate_count,
            "deferred_candidate_count": self.deferred_candidate_count,
            "candidate_inventory_recorded": self.candidate_inventory_recorded,
            "continuation_coverage": ("not_recorded" if not self.candidate_inventory_recorded else
                                      "pending" if self.deferred_candidate_count or unjudged
                                      else "judged"),
            "accepted_continuation_count": self.accepted_continuation_count,
            "skip_reason": self.skip_reason,
            "error_code": self.error_code,
            "normalization_codes": list(self.normalization_codes),
        }


def _lock_for(
    store: MemoryV2Store, source_namespace: str, active_date: str
) -> asyncio.Lock:
    key = (
        str(Path(store.db_path).resolve()),
        source_namespace,
        active_date,
        id(asyncio.get_running_loop()),
    )
    lock = _SETTLEMENT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SETTLEMENT_LOCKS[key] = lock
    return lock


def _receipt_matches(receipt: dict, *, plan: Any, assembly: Any) -> bool:
    return all(
        (
            receipt.get("id") == resolve_day_settlement_id(plan),
            receipt.get("source_namespace") == plan.source_namespace,
            receipt.get("active_date") == plan.active_date,
            receipt.get("settlement_version") == plan.settlement_version,
            receipt.get("assembler_version") == assembly.assembler_version,
            receipt.get("prompt_version") == day_settlement_prompt_version(plan.active_date),
            receipt.get("source_digest") == plan.source_digest,
            receipt.get("input_digest") == day_settlement_input_digest(plan),
            receipt.get("execution_digest") == day_settlement_execution_digest(plan),
        )
    )


async def settle_completed_day(
    store: MemoryV2Store,
    *,
    active_date: str,
    completed_before_active_date: str,
    model_call: DaySettlementModelCall,
    source_namespace: str = "mainline",
    max_output_tokens: int = DEFAULT_DAY_SETTLEMENT_MAX_OUTPUT_TOKENS,
) -> DaySettlementOrchestrationResult:
    """Resume, run at most once, and atomically commit one completed active day."""

    async with _lock_for(store, source_namespace, active_date):
        try:
            assembly = DaySettlementPlanAssembler(store.db_path).assemble(
                active_date=active_date,
                completed_before_active_date=completed_before_active_date,
                source_namespace=source_namespace,
            )
        except DaySettlementAssemblyError as exc:
            return DaySettlementOrchestrationResult(
                status="assembly_error",
                active_date=active_date,
                source_namespace=source_namespace,
                error_code=type(exc).__name__,
            )
        if not assembly.ready:
            return DaySettlementOrchestrationResult(
                status="skipped",
                active_date=active_date,
                source_namespace=source_namespace,
                source_digest=assembly.source_digest,
                skip_reason=assembly.skip_reason,
            )

        plan = assembly.plan
        assert plan is not None
        settlement_id = resolve_day_settlement_id(plan)
        input_digest = day_settlement_input_digest(plan)
        execution_digest = day_settlement_execution_digest(plan)
        receipt = store.get_day_settlement_receipt_for_day(
            source_namespace=source_namespace,
            active_date=active_date,
            settlement_version=plan.settlement_version,
            prompt_version=day_settlement_prompt_version(active_date),
        )
        if receipt is not None:
            # A successful new-day append changes the prior singleton/thread
            # projection itself. The immutable day result remains cached while
            # its own day sources are unchanged; edge repairs are separate work.
            frozen_day_matches = active_date >= "2026-10-07" and all((
                receipt.get("source_digest") == plan.source_digest,
                receipt.get("source_namespace") == source_namespace,
                receipt.get("active_date") == active_date,
                receipt.get("settlement_version") == plan.settlement_version,
                receipt.get("assembler_version") == assembly.assembler_version,
                receipt.get("prompt_version") == day_settlement_prompt_version(active_date),
            ))
            if frozen_day_matches and not store.day_settlement_sources_current(str(receipt["id"])):
                return DaySettlementOrchestrationResult(
                    status="receipt_conflict", active_date=active_date, source_namespace=source_namespace,
                    source_digest=plan.source_digest, settlement_id=str(receipt["id"]),
                    execution_digest=str(receipt["execution_digest"]),
                    error_code="prior_candidate_sources_changed",
                )
            if not frozen_day_matches and not _receipt_matches(receipt, plan=plan, assembly=assembly):
                return DaySettlementOrchestrationResult(
                    status="receipt_conflict",
                    active_date=active_date,
                    source_namespace=source_namespace,
                    source_digest=plan.source_digest,
                    settlement_id=settlement_id,
                    execution_digest=execution_digest,
                    error_code="immutable_receipt_mismatch",
                )
            coverage = store.get_day_continuation_counts(str(receipt["id"]))
            return DaySettlementOrchestrationResult(
                status="cached",
                active_date=active_date,
                source_namespace=source_namespace,
                source_digest=plan.source_digest,
                settlement_id=str(receipt["id"]),
                execution_digest=str(receipt["execution_digest"]),
                receipt_persisted=True,
                compact_item_count=int(receipt["compact_item_count"]),
                candidate_count=int(receipt["candidate_count"]),
                judgment_count=int(receipt["judgment_count"]),
                accepted_continuation_count=int(
                    receipt["accepted_continuation_count"]
                ),
                deferred_candidate_count=coverage["deferred_candidate_count"],
                candidate_inventory_recorded=coverage["inventory_recorded"],
                remaining_in_budget_count=coverage["unjudged_candidate_count"],
            )

        from .day_settlement_checkpoint import DaySettlementCheckpoint, CheckpointError, CheckpointedDayCall
        calls = 0
        try:
            checkpoint = DaySettlementCheckpoint(store.db_path, assembly, max_output_tokens)
            run_result = checkpoint.load()
            if run_result is None:
                # If output persistence fails or the process stops, this marker
                # prevents an unknown paid result from becoming a cache miss.
                durable_call = CheckpointedDayCall(checkpoint, model_call)
                run_result = await run_day_settlement(
                    assembly, model_call=durable_call, max_output_tokens=max_output_tokens,
                )
                calls = durable_call.calls
                if durable_call.persistence_error is not None:
                    raise CheckpointError('checkpoint_return_not_saved') from durable_call.persistence_error
                # The run receipt describes the original generation. `calls`
                # separately reports this invocation, including zero-call replay.
                if run_result.status == "completed" and run_result.parsed is not None:
                    checkpoint.save_validated(run_result)
        except (OSError, CheckpointError) as exc:
            return DaySettlementOrchestrationResult(
                status="checkpoint_error", active_date=active_date, source_namespace=source_namespace,
                source_digest=plan.source_digest, settlement_id=settlement_id,
                execution_digest=execution_digest, model_call_count=calls, error_code=type(exc).__name__,
            )
        if run_result.status != "completed" or run_result.parsed is None:
            return DaySettlementOrchestrationResult(
                status=run_result.status,
                active_date=active_date,
                source_namespace=source_namespace,
                source_digest=plan.source_digest,
                settlement_id=settlement_id,
                execution_digest=execution_digest,
                model_call_count=calls,
                candidate_count=len(plan.continuation_candidates),
                error_code=run_result.error_code,
                normalization_codes=run_result.normalization_codes,
            )
        try:
            continuation_seed = None
            if hasattr(model_call, 'review_continuations') and active_date >= '2026-10-17':
                from .continuation_pages import judgments_payload
                continuation_seed = judgments_payload(run_result.parsed.thread_judgments)
                # The paid first pass and its day compact survive an unavailable
                # reviewer. No proposed edge is published before independent review.
                run_result = replace(run_result, parsed=replace(run_result.parsed, thread_judgments=()))
            elif hasattr(model_call, 'review_continuations'):
                before_review = getattr(model_call, 'review_call_count', 0)
                try:
                    reviewed = await model_call.review_continuations(store, plan, run_result.parsed.thread_judgments)
                finally:
                    calls += getattr(model_call, 'review_call_count', 0) - before_review
                run_result = replace(run_result, parsed=replace(run_result.parsed, thread_judgments=reviewed))
            draft = prepare_day_settlement_draft(assembly, run_result)
            store.commit_day_settlement(draft, **({'continuation_seed': continuation_seed}
                                                if continuation_seed is not None else {}))
        except Exception as exc:
            return DaySettlementOrchestrationResult(
                status="commit_error",
                active_date=active_date,
                source_namespace=source_namespace,
                source_digest=plan.source_digest,
                settlement_id=settlement_id,
                execution_digest=execution_digest,
                model_call_count=calls,
                compact_item_count=len(run_result.parsed.day_compact),
                candidate_count=len(plan.continuation_candidates),
                judgment_count=len(run_result.parsed.thread_judgments),
                accepted_continuation_count=len(
                    run_result.parsed.accepted_continuations()
                ),
                error_code=type(exc).__name__,
                normalization_codes=run_result.normalization_codes,
            )
        return DaySettlementOrchestrationResult(
            status="committed",
            active_date=active_date,
            source_namespace=source_namespace,
            source_digest=plan.source_digest,
            settlement_id=settlement_id,
            execution_digest=execution_digest,
            model_call_count=calls,
            receipt_persisted=True,
            compact_item_count=len(run_result.parsed.day_compact),
            candidate_count=len(plan.continuation_candidates),
            judgment_count=len(run_result.parsed.thread_judgments),
            accepted_continuation_count=len(
                run_result.parsed.accepted_continuations()
            ),
            normalization_codes=run_result.normalization_codes,
            scene_groups=tuple({"summary": group.summary,
                "message_ids": list(dict.fromkeys(ref.message_id for event in plan.day_events
                    if event.event_id in group.event_ids for ref in event.sources))}
                for group in run_result.parsed.scene_groups),
            deferred_candidate_count=len(assembly.candidate_selection.deferred_candidates),
            candidate_inventory_recorded=active_date >= "2026-10-07",
        )
