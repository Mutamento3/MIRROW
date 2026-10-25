"""Low-frequency production ingestion for completed Memory V2 active days.

The online boundary is deliberately day-level.  Current conversation rows stay
in raw context; only a date strictly older than the caller's conservative
cutoff may be encoded and settled.  This keeps model calls out of the per-turn
path and prevents a late message from changing an already frozen batch.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .conversation_source import ConversationSource
from .day_backfill_runner import run_backfill_day
from .day_repair_runner import run_day_repair
from .day_repair import day_authority_digest
from .day_source_coverage import inspect_day_source_coverage
from .day_settlement import DAY_SETTLEMENT_VERSION
from .day_settlement_runner import (SETTLEMENT_PROMPT_VERSIONS, day_settlement_prompt_version,
                                    safe_day_settlement_provider_error)
from .store import MemoryV2Store
from .online_recovery import OnlineRetryReceipts, RetryDeferred, recovery_signature


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_online_lock = asyncio.Lock()


class OnlineIngestError(RuntimeError):
    """A safe, body-free completed-day ingestion failure."""


@dataclass(frozen=True)
class OnlineIngestConfiguration:
    memory_db_path: Path
    authority_db_path: Path
    max_days_per_run: int = 3
    max_requests_per_batch: int = 4
    repair_stage_root: Path | None = None
    max_continuation_pages_per_run: int = 1

    @property
    def resolved_repair_stage_root(self) -> Path:
        if self.repair_stage_root is not None:
            return self.repair_stage_root.resolve()
        return (self.memory_db_path.parent / ".memory_v2_day_repair_staging").resolve()

    def validate(self) -> None:
        if not self.memory_db_path.is_file():
            raise ValueError("memory_v2_db_missing")
        if not self.authority_db_path.is_file():
            raise ValueError("conversation_authority_missing")
        if self.max_days_per_run <= 0:
            raise ValueError("max_days_per_run_must_be_positive")
        if self.max_requests_per_batch <= 0:
            raise ValueError("max_requests_per_batch_must_be_positive")
        if type(self.max_continuation_pages_per_run) is not int or self.max_continuation_pages_per_run < 0:
            raise ValueError("max_continuation_pages_per_run_must_be_nonnegative")
        if self.resolved_repair_stage_root.is_file():
            raise ValueError("repair_stage_root_must_be_a_directory")


@dataclass(frozen=True)
class OnlineIngestResult:
    status: str
    trigger: str
    completed_before_active_date: str
    candidate_count: int = 0
    selected_count: int = 0
    processed_count: int = 0
    processed_active_dates: tuple[str, ...] = ()
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    event_count: int = 0
    repair_candidate_count: int = 0
    repaired_count: int = 0
    ingested_count: int = 0
    refresh_status: str = "not_requested"

    def safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trigger": self.trigger,
            "completed_before_active_date": self.completed_before_active_date,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "processed_count": self.processed_count,
            "processed_active_dates": list(self.processed_active_dates),
            "request_count": self.request_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "event_count": self.event_count,
            "repair_candidate_count": self.repair_candidate_count,
            "repaired_count": self.repaired_count,
            "ingested_count": self.ingested_count,
            "refresh_status": self.refresh_status,
        }


DayRunner = Callable[..., Awaitable[dict[str, Any]]]
RepairRunner = Callable[..., Awaitable[Any]]
Refresher = Callable[[Sequence[str | Path]], dict[str, object]]


def _refresh_committed_memory(configuration, refresher):
    if refresher is None:
        from .context_shadow import refresh_context_shadow_for_source_commit
        refresher = refresh_context_shadow_for_source_commit
    try:
        refresh = refresher((configuration.memory_db_path, configuration.authority_db_path))
        return str(refresh.get('status') or 'unknown')[:80]
    except Exception as exc:
        return f'error:{type(exc).__name__}'


def online_ingest_enabled() -> bool:
    return (
        os.environ.get("MIRROW_MEMORY_V2_ONLINE_INGEST", "")
        .strip()
        .casefold()
        in _TRUE_VALUES
    )


def _positive_int_from_environment(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name.lower()}_must_be_an_integer") from exc
    if value <= 0:
        raise ValueError(f"{name.lower()}_must_be_positive")
    return value


def configuration_from_environment() -> OnlineIngestConfiguration | None:
    if not online_ingest_enabled():
        return None
    backend_root = Path(__file__).resolve().parents[1]
    raw_paths = (
        os.environ.get("MIRROW_MEMORY_V2_DBS", "").strip()
        or os.environ.get("MIRROW_MEMORY_V2_SHADOW_DBS", "").strip()
    )
    paths = tuple(
        Path(value).resolve()
        for value in raw_paths.split(os.pathsep)
        if value.strip()
    )
    if len(paths) != 1:
        raise ValueError("online_ingest_requires_one_memory_v2_db")
    configuration = OnlineIngestConfiguration(
        memory_db_path=paths[0],
        authority_db_path=Path(
            os.environ.get(
                "MIRROW_MEMORY_V2_AUTHORITY_DB",
                str(backend_root / "events" / "event_chronicle.db"),
            )
        ).resolve(),
        max_days_per_run=_positive_int_from_environment(
            "MIRROW_MEMORY_V2_ONLINE_MAX_DAYS_PER_RUN", 3
        ),
        max_requests_per_batch=_positive_int_from_environment(
            "MIRROW_MEMORY_V2_ONLINE_MAX_REQUESTS_PER_BATCH", 4
        ),
        repair_stage_root=Path(
            os.environ.get(
                "MIRROW_MEMORY_V2_REPAIR_STAGE_ROOT",
                str(backend_root / ".tmp" / "memory_v2_day_repair"),
            )
        ).resolve(),
        max_continuation_pages_per_run=int(os.environ.get("MIRROW_MEMORY_V2_CONTINUATION_MAX_PAGES_PER_RUN", "1").strip() or "1"),
    )
    configuration.validate()
    return configuration


def conservative_completed_before_active_date(
    now: datetime,
    *,
    active_topic_dates: Sequence[str] = (),
    day_change_hour: int = 6,
    explicitly_closed_active_date: str = "",
) -> str:
    """Return the earliest date that is not proven complete.

    Before 06:00, the previous calendar day may still own a cross-midnight
    conversation. An eligible explicit day close advances that clock boundary;
    any currently active topic still lowers the cutoff further.
    """

    if not 0 <= day_change_hour <= 23:
        raise ValueError("day_change_hour_out_of_range")
    clock_cutoff = now.date()
    if now.hour < day_change_hour:
        clock_cutoff -= timedelta(days=1)
    if explicitly_closed_active_date:
        try:
            closed_date = date.fromisoformat(explicitly_closed_active_date)
        except ValueError as exc:
            raise ValueError("closed_active_date_must_be_iso") from exc
        if closed_date > now.date():
            raise ValueError("closed_active_date_must_not_be_future")
        clock_cutoff = max(clock_cutoff, closed_date + timedelta(days=1))
    candidates = [clock_cutoff]
    for value in active_topic_dates:
        try:
            candidates.append(date.fromisoformat(str(value)))
        except ValueError as exc:
            raise ValueError("active_topic_date_must_be_iso") from exc
    return min(candidates).isoformat()


def _usage_value(report: dict[str, Any], key: str) -> int:
    usage = report.get("usage")
    raw_value = usage.get(key) if isinstance(usage, dict) else report.get(key)
    try:
        return int(raw_value or 0)
    except (TypeError, ValueError):
        return 0


async def ingest_completed_days(
    configuration: OnlineIngestConfiguration,
    *,
    completed_before_active_date: str,
    trigger: str,
    day_runner: DayRunner = run_backfill_day,
    repair_runner: RepairRunner = run_day_repair,
    refresher: Refresher | None = None,
    explicit_retry: bool = False,
    on_work_started: Callable[[str], None] | None = None,
    on_continuation_started: Callable[[str], None] | None = None,
) -> OnlineIngestResult:
    """Encode fresh days first; independent old repairs cannot block their lane."""

    configuration.validate()
    try:
        cutoff = date.fromisoformat(str(completed_before_active_date)).isoformat()
    except ValueError as exc:
        raise ValueError("completed_before_active_date_must_be_iso") from exc
    safe_trigger = str(trigger or "unspecified")[:80]
    startup_recovery = safe_trigger == "startup"

    async with _online_lock:
        source = ConversationSource(
            configuration.authority_db_path,
            canonical_sessions_only=True,
        )
        from .scene_planner import planner_for_ingest
        planner = planner_for_ingest(source)
        # The up-to-date path must be genuinely read-only: do not execute even
        # idempotent DDL on every startup/daily check.  The day runner performs
        # normal schema initialisation only after a missing completed day exists.
        store = MemoryV2Store(configuration.memory_db_path, initialise=False)
        repair_candidates = tuple(
            active_date
            for active_date in store.list_pending_source_rebuild_dates(
                source_namespace=planner.config.source_namespace
            )
            if active_date < cutoff
        )
        source_dates = source.list_active_dates(before_active_date=cutoff)
        settled_dates = {
            active_date
            for version in SETTLEMENT_PROMPT_VERSIONS
            for active_date in store.list_settled_active_dates(
                source_namespace=planner.config.source_namespace,
                settlement_version=DAY_SETTLEMENT_VERSION,
                prompt_version=version,
                before_active_date=cutoff,
            )
            if day_settlement_prompt_version(active_date) == version
        }
        repair_set = set(repair_candidates)
        completed_sources: set[str] = set()
        verified_sources: set[str] = set()
        # Completion receipts are a watermark, not proof that no source was
        # appended later (for example a retried domain-event projection).
        for active_date in sorted(set(source_dates) | settled_dates):
            day = planner.plan_active_date(active_date)
            coverage = inspect_day_source_coverage(
                configuration.memory_db_path, day,
                source_namespace=planner.config.source_namespace,
                authority_digest=day_authority_digest(day),
            )
            if coverage.source_changed:
                repair_set.add(active_date)
            elif coverage.zero_event_complete or coverage.repaired_complete:
                completed_sources.add(active_date)
            if coverage.zero_event_complete or coverage.repaired_complete or coverage.manifest_complete:
                verified_sources.add(active_date)
        repair_candidates = tuple(sorted(repair_set))
        retries = OnlineRetryReceipts(configuration.memory_db_path)
        from .online_reconstruction import reconstruction_recovery_dates
        retry_records = retries._read()
        derived_pending, derived_completed = reconstruction_recovery_dates(
            source, settled_dates, retry_records, cutoff=cutoff)
        # The scene runtime may have finished an earlier failed day independently.
        # Verify canonical coverage and frozen scene receipts before retiring it.
        reconciled = derived_completed - repair_set
        # Restoring frozen receipt wording can prove an earlier encode repair
        # was unnecessary. Retire only that obsolete failure, not other phases.
        reconciled |= {day for day in verified_sources - repair_set
                       if retry_records.get(day, {}).get('status') == 'error'
                       and str(retry_records[day].get('error_code', '')).startswith('staging_error:encode:')}
        refresh_status = 'not_requested'
        if reconciled:
            refresh_status = _refresh_committed_memory(configuration, refresher)
            for active_date in reconciled:
                retries.succeeded(active_date)
        ingest_candidates = tuple(
            day
            for day in source_dates
            if day not in repair_set and
            ((day not in settled_dates and day not in completed_sources) or day in derived_pending)
        )
        from .continuation_raw_checkpoint import returned_dates
        from contextlib import closing
        with closing(store._connect()) as recovery_connection:
            raw_recovery_dates = set(returned_dates(recovery_connection, planner.config.source_namespace, cutoff))
        continuation_dates = set(raw_recovery_dates)
        if configuration.max_continuation_pages_per_run:
            if startup_recovery:
                continuation_dates.update(store.list_continuation_recovery_dates(
                    source_namespace=planner.config.source_namespace, before_active_date=cutoff))
            else:
                continuation_dates.update(store.list_pending_continuation_dates(
                    source_namespace=planner.config.source_namespace, before_active_date=cutoff, explicit_retry=explicit_retry))
                continuation_dates.update(store.list_continuation_replan_dates(
                    source_namespace=planner.config.source_namespace, before_active_date=cutoff))
        from .continuation_queue import ordered_dates
        continuation_order = ordered_dates(configuration.memory_db_path, continuation_dates,
                                           source_namespace=planner.config.source_namespace)
        continuation_candidates = tuple(day for day in continuation_order
                                        if day not in repair_set and day not in ingest_candidates)
        candidates = tuple(
            sorted(
                (
                    *((active_date, "repair") for active_date in repair_candidates),
                    *((active_date, "ingest") for active_date in ingest_candidates),
                    *((active_date, "continuation") for active_date in continuation_candidates),
                ),
                key=lambda item: ({'ingest': 0, 'repair': 1, 'continuation': 2}[item[1]],
                                  continuation_order.index(item[0]) if item[1] == 'continuation' else item[0]),
            )
        )
        # Reserve the independent page lane: fresh encoding cannot consume its seats or budget.
        selected = [item for item in candidates if item[1] != "continuation"]
        # Deferred / zero-call maintenance must not occupy the only provider slot.
        # The global actual-call budget below remains the admission boundary.
        selected += [item for item in candidates if item[1] == "continuation"]
        if not selected:
            return OnlineIngestResult(
                status="up_to_date",
                trigger=safe_trigger,
                completed_before_active_date=cutoff,
                candidate_count=0,
                selected_count=0,
                repair_candidate_count=0,
                refresh_status=refresh_status,
            )

        processed: list[str] = []
        request_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        event_count = 0
        repaired_count = 0
        ingested_count = 0
        committed_memory = False
        continuation_budget = configuration.max_continuation_pages_per_run
        day_attempts = 0
        continuation_attempts = 0
        failures: list[str] = []
        try:
            for work_index, (active_date, work_kind) in enumerate(selected):
                if work_kind == "continuation" and continuation_budget <= 0 and active_date not in raw_recovery_dates:
                    continue
                if work_kind != 'continuation' and day_attempts >= configuration.max_days_per_run:
                    continue
                if (work_kind == 'continuation' and not explicit_retry
                        and retries._read().get(active_date, {}).get('operator_hold')):
                    failures.append(f'completed_day_continuation_deferred:{active_date}:operator_retry_required')
                    continue
                if work_kind == 'continuation' and active_date in raw_recovery_dates:
                    previous = retries._read().get(active_date, {})
                    if previous.get('operator_hold'):
                        failures.append(f'completed_day_continuation_deferred:{active_date}:operator_retry_required')
                        continue
                    from .continuation_ingest import run_continuation_day
                    recovered = await run_continuation_day(store, active_date=active_date,
                        source_namespace=planner.config.source_namespace, max_model_calls=0,
                        completed_before_active_date=cutoff)
                    continuation_attempts += 1
                    committed_memory |= bool(recovered.get('temporary_commit_valid'))
                    if recovered.get('status') == 'ok':
                        processed.append(active_date)
                        # Only a fully finished known continuation failure is retired here.
                        if ((recovered.get('continuation_report') or {}).get('status') == 'complete'
                                and str(previous.get('error_code', '')).startswith('continuation_')):
                            retries.succeeded(active_date)
                    else:
                        failures.append(f'completed_day_continuation_failed:{active_date}:saved_output_unresolved')
                    continue  # A returned body never grants a new call in this window.
                target_day = planner.plan_active_date(active_date)
                from .continuation_pages import PAGE_PROMPT_VERSION
                continuation_identity = (store.continuation_work_digest(
                    source_namespace=planner.config.source_namespace, active_date=active_date)
                    if work_kind == "continuation" else "")
                signature = recovery_signature(
                    authority_digest=day_authority_digest(target_day) + continuation_identity,
                    prompt_versions=[day_settlement_prompt_version(active_date),
                                     *([PAGE_PROMPT_VERSION] if work_kind == "continuation" else []),
                                     *[plan.batch.prompt_version for plan in target_day.batches]],
                    work_kind=work_kind,
                )
                try:
                    retries.begin(active_date, signature, explicit_retry=explicit_retry)
                except RetryDeferred as exc:
                    failures.append(f'completed_day_{work_kind}_deferred:{active_date}:{exc}')
                    continue
                if work_kind != 'continuation':
                    day_attempts += 1
                else:
                    continuation_attempts += 1
                notify = (on_continuation_started if work_kind == "continuation" and not startup_recovery
                          and on_continuation_started is not None else on_work_started)
                if notify is not None:
                    notify(active_date)
                if work_kind == "repair":
                    raw_report = await repair_runner(
                        source,
                        memory_db_path=configuration.memory_db_path,
                        stage_root=configuration.resolved_repair_stage_root,
                        active_date=active_date,
                        completed_before_active_date=cutoff,
                        max_requests_per_batch=configuration.max_requests_per_batch,
                    )
                    report = (
                        raw_report.safe_dict()
                        if hasattr(raw_report, "safe_dict")
                        else dict(raw_report)
                    )
                    success = report.get("status") == "ok"
                elif work_kind == "continuation":
                    from .continuation_ingest import run_continuation_day
                    report = await run_continuation_day(store, active_date=active_date,
                        source_namespace=planner.config.source_namespace,
                        max_model_calls=0 if startup_recovery else continuation_budget,
                        explicit_retry=explicit_retry, completed_before_active_date=cutoff)
                    success = report.get("status") == "ok"
                    committed_memory |= bool(report.get("temporary_commit_valid"))
                else:
                    report = await day_runner(
                        source,
                        planner,
                        active_date,
                        max_requests=configuration.max_requests_per_batch,
                        work_db_path=configuration.memory_db_path,
                        repair_quality=False,
                        completed_before_active_date=cutoff,
                        **({'explicit_retry': True} if explicit_retry else {}),
                        max_continuation_pages=0,
                    )
                    success = report.get("status") == "ok" and bool(
                        report.get("temporary_commit_valid")
                    )
                    committed_memory |= bool(report.get('temporary_commit_valid'))
                continuation_budget -= int((report.get("continuation_report") or {}).get("model_call_count") or 0)
                request_count += int(report.get("request_count") or 0)
                prompt_tokens += _usage_value(report, "prompt_tokens")
                completion_tokens += _usage_value(report, "completion_tokens")
                event_count += int(report.get("event_count") or 0)
                if not success:
                    code = str(report.get("status") or "unknown")[:80]
                    if work_kind == "repair" and code == "staging_error":
                        from .repair_diagnostics import repair_failure_code
                        code = repair_failure_code(report)
                    if code == "day_settlement_provider_error":
                        detail = safe_day_settlement_provider_error(report.get("error"))
                        if detail:
                            code += f":{detail}"
                    if work_kind == "ingest" and code == "batch_encoding_error":
                        batch_number = report.get("failed_batch_number")
                        reports = report.get("encoding_reports")
                        last = reports[-1] if isinstance(reports, list) and reports else {}
                        stage = last.get("status") if isinstance(last, dict) else None
                        if isinstance(batch_number, int) and batch_number > 0:
                            code += f":batch_{batch_number}"
                        if stage in {"contract_error", "provider_error", "assembly_error",
                                     "request_limit", "quality_cache_conflict"}:
                            code += f":{stage}"
                        if stage == "contract_error":
                            shards = last.get("shards") or []
                            detail = shards[-1].get("error_code") if shards else None
                            if detail in {"human_actor_evidence_missing", "k_actor_evidence_missing",
                                          "encoding_contract_rejected"}:
                                code += f":{detail}"
                            if detail == 'encoding_contract_rejected' and shards:
                                from .online_recovery import safe_contract_check
                                code += ':' + safe_contract_check(shards[-1].get('error', ''))
                    retries.failed(active_date, error_code=code, report=report)
                    failures.append(f"completed_day_{work_kind}_failed:{active_date}:{code}")
                    continue
                retries.succeeded(active_date)
                processed.append(active_date)
                if work_kind == "repair":
                    repaired_count += 1
                elif work_kind == "ingest":
                    ingested_count += 1
                if work_kind in {"ingest", "repair"} and continuation_budget > 0:
                    # A new settlement may just have atomically saved its seed.
                    # Re-read the remaining lane without restarting its budget.
                    fresh = set(store.list_continuation_recovery_dates(
                        source_namespace=planner.config.source_namespace, before_active_date=cutoff))
                    if not startup_recovery:
                        fresh.update(store.list_pending_continuation_dates(
                            source_namespace=planner.config.source_namespace,
                            before_active_date=cutoff, explicit_retry=explicit_retry))
                    rest = selected[work_index + 1:]
                    fresh.update(day for day, kind in rest if kind == "continuation")
                    excluded = {day for day, kind in rest if kind != "continuation"}
                    excluded.update(day for day in repair_set if day not in processed)
                    ordered = ordered_dates(configuration.memory_db_path, fresh - excluded,
                                            source_namespace=planner.config.source_namespace)
                    selected[work_index + 1:] = ([item for item in rest if item[1] != "continuation"] +
                                                [(day, "continuation") for day in ordered])
        finally:
            if processed or committed_memory:
                refresh_status = _refresh_committed_memory(configuration, refresher)

        if failures:
            # Keep unresolved work visible; a successful independent day is not a false all-clear.
            prefix = f'completed_days_partial:processed_{len(processed)};' if processed else ''
            raise OnlineIngestError(prefix + ';'.join(failures[:4]))

        return OnlineIngestResult(
            status="processed",
            trigger=safe_trigger,
            completed_before_active_date=cutoff,
            candidate_count=len(candidates),
            selected_count=day_attempts + continuation_attempts,
            processed_count=len(processed),
            processed_active_dates=tuple(processed),
            request_count=request_count,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            event_count=event_count,
            repair_candidate_count=len(repair_candidates),
            repaired_count=repaired_count,
            ingested_count=ingested_count,
            refresh_status=refresh_status,
        )


async def ingest_completed_days_from_environment(
    *,
    completed_before_active_date: str,
    trigger: str,
    on_work_started: Callable[[str], None] | None = None,
    on_continuation_started: Callable[[str], None] | None = None,
) -> OnlineIngestResult:
    configuration = configuration_from_environment()
    if configuration is None:
        return OnlineIngestResult(
            status="disabled",
            trigger=str(trigger or "unspecified")[:80],
            completed_before_active_date=str(completed_before_active_date),
        )
    return await ingest_completed_days(
        configuration,
        completed_before_active_date=completed_before_active_date,
        trigger=trigger,
        on_work_started=on_work_started,
        on_continuation_started=on_continuation_started,
    )


__all__ = [
    "OnlineIngestConfiguration",
    "OnlineIngestError",
    "OnlineIngestResult",
    "configuration_from_environment",
    "conservative_completed_before_active_date",
    "ingest_completed_days",
    "ingest_completed_days_from_environment",
    "online_ingest_enabled",
]
