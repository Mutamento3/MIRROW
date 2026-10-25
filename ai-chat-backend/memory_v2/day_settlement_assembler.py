"""Read-only assembly of completed-day settlement plans from Memory V2 SQLite."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .day_settlement import (
    MAX_DAY_EVENTS,
    MAX_PRIOR_THREADS,
    MAX_PRIOR_EVENTS,
    DaySettlementContractError,
    DaySettlementPlan,
    SettlementEvent,
    SettlementThreadCandidate,
)
from .day_settlement_candidates import (
    DaySettlementCandidatePolicy,
    DaySettlementCandidateSelection,
    CANDIDATE_COVERAGE_START_ACTIVE_DATE,
    common_settlement_terms,
    screen_settlement_event_pair,
    select_day_settlement_candidates,
)
from .day_settlement_lineage import (
    ASSEMBLER_VERSION,
    day_source_digest,
    effective_event_status_sql,
    effective_event_thread_status_sql,
    has_event_thread_status_log,
    load_day_lineage_rows,
)
from .models import SourceRef, ContinuationCandidateReceiptDraft
from .continuation_inventory import freeze_candidate_inventory, ContinuationInventoryError


CONTINUITY_THREAD_TYPE = "scene_continuation"
_SUPPORTED_SCHEMA_VERSIONS = {6, 7, 8, 9, 10, 11, 12, 13}
_REQUIRED_TABLES = {
    "memory_schema",
    "encoding_batches",
    "events",
    "event_sources",
    "event_participants",
    "event_status_log",
    "event_threads",
    "thread_events",
}


class DaySettlementAssemblyError(RuntimeError):
    """The read model cannot prove a complete settlement input."""


@dataclass(frozen=True)
class DaySettlementAssembly:
    active_date: str
    source_namespace: str
    source_digest: str
    plan: DaySettlementPlan | None
    candidate_selection: DaySettlementCandidateSelection | None
    completed_batch_count: int
    active_event_count: int
    eligible_thread_count: int
    projected_thread_count: int
    skip_reason: str = ""
    assembler_version: str = ASSEMBLER_VERSION
    eligible_prior_event_count: int = 0
    projected_prior_event_count: int = 0
    candidate_inventory: tuple[ContinuationCandidateReceiptDraft, ...] = ()

    @property
    def ready(self) -> bool:
        return self.plan is not None

    def safe_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "active_date": self.active_date,
            "source_namespace": self.source_namespace,
            "source_digest": self.source_digest,
            "completed_batch_count": self.completed_batch_count,
            "active_event_count": self.active_event_count,
            "eligible_thread_count": self.eligible_thread_count,
            "projected_thread_count": self.projected_thread_count,
            "eligible_prior_event_count": self.eligible_prior_event_count,
            "projected_prior_event_count": self.projected_prior_event_count,
            "ready": self.ready,
            "skip_reason": self.skip_reason,
            "assembler_version": self.assembler_version,
        }
        if self.candidate_selection is not None:
            result["candidate_selection"] = self.candidate_selection.safe_dict()
        return result


def _short_ref(prefix: str, identity: str) -> str:
    return prefix + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:15]


def _timestamp(value: str) -> datetime:
    from .source_time import instant
    return instant(value)


class DaySettlementPlanAssembler:
    """Strictly read Memory V2 and construct one bounded settlement plan."""

    def __init__(self, db_path: str | Path):
        path = Path(db_path).resolve()
        if not path.is_file():
            raise DaySettlementAssemblyError(
                f"Memory V2 database does not exist: {path}"
            )
        self.db_path = path
        self._verify_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        uri = f"{self.db_path.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _verify_schema(self) -> None:
        try:
            with self._connect() as connection:
                tables = {
                    str(row["name"])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing = _REQUIRED_TABLES - tables
                if missing:
                    raise DaySettlementAssemblyError(
                        "Memory V2 database is missing settlement tables: "
                        + ",".join(sorted(missing))
                    )
                row = connection.execute(
                    "SELECT schema_version FROM memory_schema "
                    "WHERE schema_key='memory_v2'"
                ).fetchone()
                if row is None or int(row["schema_version"]) not in _SUPPORTED_SCHEMA_VERSIONS:
                    version = "missing" if row is None else str(row["schema_version"])
                    raise DaySettlementAssemblyError(
                        f"unsupported Memory V2 settlement schema: {version}"
                    )
                if (
                    int(row["schema_version"]) >= 9
                    and "event_thread_status_log" not in tables
                ):
                    raise DaySettlementAssemblyError(
                        "Memory V2 database is missing settlement tables: "
                        "event_thread_status_log"
                    )
        except sqlite3.Error as exc:
            raise DaySettlementAssemblyError(
                f"cannot inspect Memory V2 settlement source: {exc}"
            ) from exc

    @staticmethod
    def _validate_completed_date(
        active_date: str, completed_before_active_date: str
    ) -> None:
        try:
            target = date.fromisoformat(active_date)
            cutoff = date.fromisoformat(completed_before_active_date)
        except ValueError as exc:
            raise DaySettlementAssemblyError(
                "settlement dates must use ISO YYYY-MM-DD"
            ) from exc
        if target >= cutoff:
            raise DaySettlementAssemblyError(
                "target active day is not proven complete by the supplied cutoff"
            )

    @staticmethod
    def _load_detail_maps(
        connection: sqlite3.Connection,
        event_ids: Sequence[str],
    ) -> tuple[dict[str, tuple[SourceRef, ...]], dict[str, tuple[str, ...]]]:
        source_lists: dict[str, list[SourceRef]] = defaultdict(list)
        participant_lists: dict[str, list[str]] = defaultdict(list)
        for offset in range(0, len(event_ids), 500):
            chunk = event_ids[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            for row in connection.execute(
                "SELECT event_id, message_row_id, message_id, session_id, source_ts, "
                "source_role, source_kind, source_event_type, scene_id, span_start, "
                "span_end, span_digest FROM event_sources "
                f"WHERE event_id IN ({placeholders}) ORDER BY event_id, source_order",
                chunk,
            ).fetchall():
                source_lists[str(row["event_id"])].append(
                    SourceRef(
                        message_row_id=int(row["message_row_id"]),
                        message_id=str(row["message_id"]),
                        session_id=str(row["session_id"]),
                        source_ts=str(row["source_ts"]),
                        source_role=str(row["source_role"]),
                        source_kind=str(row["source_kind"]),
                        source_event_type=str(row["source_event_type"]),
                        scene_id=str(row["scene_id"]),
                        span_start=row["span_start"],
                        span_end=row["span_end"],
                        span_digest=str(row["span_digest"]),
                    )
                )
            for row in connection.execute(
                "SELECT event_id, participant_id FROM event_participants "
                f"WHERE event_id IN ({placeholders}) "
                "ORDER BY event_id, participant_order",
                chunk,
            ).fetchall():
                participant_lists[str(row["event_id"])].append(
                    str(row["participant_id"])
                )
        return (
            {key: tuple(value) for key, value in source_lists.items()},
            {key: tuple(value) for key, value in participant_lists.items()},
        )

    @staticmethod
    def _settlement_event(
        row: sqlite3.Row,
        *,
        ref_prefix: str,
        source_map: dict[str, tuple[SourceRef, ...]],
        participant_map: dict[str, tuple[str, ...]],
        include_recurrence: bool = False,
    ) -> SettlementEvent:
        event_id = str(row["id"])
        attributes = json.loads(row["attributes_json"] or "{}")
        from .recurrence import validated_label
        return SettlementEvent(
            ref=_short_ref(ref_prefix, event_id),
            event_id=event_id,
            batch_id=str(row["batch_id"]),
            event_type=str(row["event_type"]),
            subject_id=str(row["subject_id"]),
            summary=str(row["summary"]),
            state_changes=tuple(attributes.get('state_changes', [])),
            reported_at=str(row["reported_at"]),
            sources=source_map.get(event_id, ()),
            participant_ids=participant_map.get(event_id, ()),
            source_scope_ids=tuple(attributes.get("source_scope_ids", ())),
            recurrence_label=(validated_label(attributes.get("recurrence_label"),
                                              attributes.get("recurrence_confidence"))
                              if include_recurrence else ""),
        )

    def _load_day_rows(
        self,
        connection: sqlite3.Connection,
        source_namespace: str,
        active_date: str,
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
        batch_rows, all_event_rows = load_day_lineage_rows(
            connection, source_namespace, active_date
        )
        incomplete = [str(row["id"]) for row in batch_rows if row["status"] != "completed"]
        if incomplete:
            raise DaySettlementAssemblyError(
                "completed active day still has incomplete encoding batches"
            )
        status_sql = effective_event_status_sql("e")
        day_rows = connection.execute(
            "SELECT e.* FROM events e JOIN encoding_batches b ON b.id=e.batch_id "
            "WHERE b.source_namespace=? AND b.active_date=? AND b.status='completed' "
            f"AND {status_sql}='active' ORDER BY e.reported_at, e.id",
            (source_namespace, active_date),
        ).fetchall()
        return list(batch_rows), list(all_event_rows), list(day_rows)

    def _load_prior_thread_rows(
        self,
        connection: sqlite3.Connection,
        source_namespace: str,
        active_date: str,
    ) -> list[list[sqlite3.Row]]:
        status_sql = effective_event_status_sql("e")
        thread_status_sql = effective_event_thread_status_sql(
            "t",
            status_log_available=has_event_thread_status_log(connection),
        )
        rows = connection.execute(
            "SELECT t.id AS thread_id, te.sequence_no, e.*, "
            f"{status_sql} AS current_status FROM event_threads t "
            "JOIN thread_events te ON te.thread_id=t.id "
            "JOIN events e ON e.id=te.event_id "
            "JOIN encoding_batches b ON b.id=e.batch_id "
            "WHERE t.namespace=? AND t.thread_type=? AND b.status='completed' "
            f"AND {thread_status_sql}='active' "
            "ORDER BY t.id, te.sequence_no",
            (source_namespace, CONTINUITY_THREAD_TYPE),
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[str(row["thread_id"])].append(row)
        eligible: list[list[sqlite3.Row]] = []
        for thread_rows in grouped.values():
            as_of_rows = [
                row
                for row in thread_rows
                if str(row["active_date"]) < active_date
            ]
            if not as_of_rows:
                continue
            actual_tail = as_of_rows[-1]
            if str(actual_tail["current_status"]) != "active":
                continue
            active_rows = [
                row for row in as_of_rows if str(row["current_status"]) == "active"
            ]
            if active_rows:
                eligible.append(active_rows)
        return eligible

    @staticmethod
    def _load_prior_event_rows(
        connection: sqlite3.Connection, source_namespace: str, active_date: str,
    ) -> list[sqlite3.Row]:
        status_sql = effective_event_status_sql("e")
        thread_status = effective_event_thread_status_sql(
            "t", status_log_available=has_event_thread_status_log(connection),
        )
        return connection.execute(
            "SELECT e.* FROM events e JOIN encoding_batches b ON b.id=e.batch_id "
            "WHERE b.source_namespace=? AND b.active_date<? AND b.status='completed' "
            f"AND {status_sql}='active' AND NOT EXISTS ("
            "SELECT 1 FROM thread_events te JOIN event_threads t ON t.id=te.thread_id "
            "WHERE te.event_id=e.id AND t.namespace=? AND t.thread_type=? "
            f"AND {thread_status}='active') ORDER BY e.reported_at, e.id",
            (source_namespace, active_date, source_namespace, CONTINUITY_THREAD_TYPE),
        ).fetchall()

    @staticmethod
    def _project_prior_events(
        day_events: Sequence[SettlementEvent], prior_events: Sequence[SettlementEvent],
        policy: DaySettlementCandidatePolicy,
    ) -> tuple[SettlementEvent, ...]:
        """One target's repeated matches cannot fill the prior singleton window."""
        ignored = common_settlement_terms((*day_events, *prior_events))
        rankings = []
        for target in day_events:
            matches = []
            for earlier in prior_events:
                if _timestamp(earlier.reported_at) > _timestamp(target.reported_at):
                    continue
                screening = screen_settlement_event_pair(
                    earlier, target, intra_day=False, ignored_terms=ignored, allow_long_gap=policy.allow_long_gap,
                    named_reference_support=policy.named_reference_support,
                )
                if screening.supported and screening.local_score >= policy.min_local_score:
                    matches.append((screening.local_score, earlier))
            matches.sort(key=lambda item: (-item[0], -_timestamp(item[1].reported_at).timestamp(), item[1].ref))
            rankings.append(matches)
        selected = {}
        for rank in range(max((len(items) for items in rankings), default=0)):
            for items in rankings:
                if rank < len(items):
                    event = items[rank][1]
                    selected[event.ref] = event
                    if len(selected) == MAX_PRIOR_EVENTS:
                        return tuple(selected.values())
        return tuple(selected.values())

    @staticmethod
    def _project_threads(
        day_events: Sequence[SettlementEvent],
        histories: Sequence[tuple[str, Sequence[SettlementEvent]]],
        policy: DaySettlementCandidatePolicy,
    ) -> tuple[SettlementThreadCandidate, ...]:
        ranked: list[tuple[float, datetime, str, SettlementThreadCandidate]] = []
        ignored_terms = common_settlement_terms(
            (
                *day_events,
                *(event for _, events in histories for event in events),
            )
        )
        for thread_id, events in histories:
            best: tuple[float, datetime, int] | None = None
            for event_index, event in enumerate(events):
                for target in day_events:
                    screening = screen_settlement_event_pair(
                        event,
                        target,
                        intra_day=False,
                        ignored_terms=ignored_terms,
                        allow_long_gap=policy.allow_long_gap,
                        named_reference_support=policy.named_reference_support,
                    )
                    if (
                        not screening.supported
                        or screening.local_score < policy.min_local_score
                    ):
                        continue
                    candidate = (
                        screening.local_score,
                        _timestamp(event.reported_at),
                        event_index,
                    )
                    if best is None or candidate[:2] > best[:2]:
                        best = candidate
            if best is None:
                continue
            selected_indexes = sorted({0, best[2], len(events) - 1})
            projection = SettlementThreadCandidate(
                ref=_short_ref("t", thread_id),
                thread_id=thread_id,
                representative_events=tuple(events[index] for index in selected_indexes),
            )
            ranked.append(
                (
                    best[0],
                    _timestamp(events[-1].reported_at),
                    thread_id,
                    projection,
                )
            )
        ranked.sort(key=lambda item: (-item[0], -item[1].timestamp(), item[2]))
        return tuple(item[3] for item in ranked[:MAX_PRIOR_THREADS])

    def assemble(
        self,
        *,
        active_date: str,
        completed_before_active_date: str,
        source_namespace: str = "mainline",
        candidate_policy: DaySettlementCandidatePolicy | None = None,
    ) -> DaySettlementAssembly:
        """Build one summary-only plan after an explicit active-day cutoff."""

        self._validate_completed_date(active_date, completed_before_active_date)
        if not source_namespace.strip():
            raise DaySettlementAssemblyError("source namespace is required")
        policy = candidate_policy or DaySettlementCandidatePolicy.for_active_date(active_date)
        from .settlement_candidate_pool import CandidatePool, ASSEMBLER_VERSION as POOL_VERSION
        assembler_version = POOL_VERSION if policy.full_history_inventory else ASSEMBLER_VERSION

        with self._connect() as connection:
            batch_rows, all_event_rows, day_rows = self._load_day_rows(
                connection, source_namespace, active_date
            )
            source_digest = day_source_digest(
                source_namespace=source_namespace,
                active_date=active_date,
                batch_rows=batch_rows,
                event_status_rows=all_event_rows,
                assembler_version=assembler_version,
            )
            if not batch_rows:
                return DaySettlementAssembly(
                    active_date=active_date,
                    source_namespace=source_namespace,
                    source_digest=source_digest,
                    plan=None,
                    candidate_selection=None,
                    completed_batch_count=0,
                    active_event_count=0,
                    eligible_thread_count=0,
                    projected_thread_count=0,
                    skip_reason="no_completed_batches",
                    assembler_version=assembler_version,
                )
            if not day_rows:
                return DaySettlementAssembly(
                    active_date=active_date,
                    source_namespace=source_namespace,
                    source_digest=source_digest,
                    plan=None,
                    candidate_selection=None,
                    completed_batch_count=len(batch_rows),
                    active_event_count=0,
                    eligible_thread_count=0,
                    projected_thread_count=0,
                    skip_reason="no_active_events",
                    assembler_version=assembler_version,
                )
            if len(day_rows) > MAX_DAY_EVENTS:
                raise DaySettlementAssemblyError(
                    "completed day exceeds the lightweight event contract"
                )

            prior_row_groups = self._load_prior_thread_rows(
                connection, source_namespace, active_date
            )
            prior_event_rows = (self._load_prior_event_rows(connection, source_namespace, active_date)
                                if active_date >= CANDIDATE_COVERAGE_START_ACTIVE_DATE else [])
            detail_event_ids = [str(row["id"]) for row in day_rows]
            detail_event_ids.extend(
                str(row["id"])
                for thread_rows in prior_row_groups
                for row in thread_rows
            )
            detail_event_ids.extend(str(row["id"]) for row in prior_event_rows)
            source_map, participant_map = self._load_detail_maps(
                connection, detail_event_ids
            )
            day_events = tuple(
                self._settlement_event(
                    row,
                    ref_prefix="d",
                    source_map=source_map,
                    participant_map=participant_map,
                    include_recurrence=active_date >= CANDIDATE_COVERAGE_START_ACTIVE_DATE,
                )
                for row in day_rows
            )
            histories = tuple(
                (
                    str(thread_rows[0]["thread_id"]),
                    tuple(
                        self._settlement_event(
                            row,
                            ref_prefix="p",
                            source_map=source_map,
                            participant_map=participant_map,
                            include_recurrence=active_date >= CANDIDATE_COVERAGE_START_ACTIVE_DATE,
                        )
                        for row in thread_rows
                    ),
                )
                for thread_rows in prior_row_groups
            )
            prior_events = tuple(self._settlement_event(
                row, ref_prefix="p", source_map=source_map, participant_map=participant_map,
                include_recurrence=True,
            ) for row in prior_event_rows)

        projected_threads = (tuple(SettlementThreadCandidate(_short_ref('t', tid), tid, nodes)
                                   for tid, nodes in histories) if policy.full_history_inventory else
                             self._project_threads(day_events, histories, policy))
        # Standalone history already has a per-target round-robin projection.
        # This change repairs the global thread truncation, not that policy.
        projected_prior_events = self._project_prior_events(day_events, prior_events, policy)
        pool_type = CandidatePool if policy.full_history_inventory else DaySettlementPlan
        unscreened_plan = pool_type(
            source_namespace=source_namespace,
            active_date=active_date,
            source_digest=source_digest,
            day_events=day_events,
            prior_threads=projected_threads,
            prior_events=projected_prior_events,
        )
        try:
            candidate_selection = select_day_settlement_candidates(
                unscreened_plan, policy
            )
        except DaySettlementContractError as exc:
            raise DaySettlementAssemblyError(
                "candidate selection produced an invalid settlement plan"
            ) from exc
        if policy.full_history_inventory:
            plan, candidate_selection = unscreened_plan.materialize(candidate_selection)
            prompt_threads = plan.prior_threads
        else:
            plan = None
        referenced_thread_refs = {
            candidate.prior_thread_ref
            for candidate in candidate_selection.candidates
            if candidate.prior_thread_ref
        }
        legacy_prompt_threads = tuple(
            thread
            for thread in projected_threads
            if thread.ref in referenced_thread_refs
        )
        match_refs = {candidate.match_event_ref for candidate in candidate_selection.candidates}
        prompt_prior_events = tuple(event for event in projected_prior_events if event.ref in match_refs)
        if plan is None:
            prompt_threads = legacy_prompt_threads
            prompt_plan = replace(unscreened_plan, prior_threads=prompt_threads, prior_events=prompt_prior_events)
            try:
                plan = candidate_selection.apply_to(prompt_plan)
            except DaySettlementContractError as exc:
                raise DaySettlementAssemblyError(
                    "candidate projection produced an invalid settlement plan"
                ) from exc
        from .day_settlement_runner import day_settlement_prompt_version
        # Freeze the full screened pool without sending deferred pairs to the
        # provider. Old dates retain their existing receipt/input contract.
        inventory = ()
        if active_date >= CANDIDATE_COVERAGE_START_ACTIVE_DATE:
            try:
                with self._connect() as connection:
                    inventory = freeze_candidate_inventory(
                        connection, plan=unscreened_plan, selection=candidate_selection,
                        prompt_version=day_settlement_prompt_version(active_date),
                    )
            except ContinuationInventoryError as exc:
                raise DaySettlementAssemblyError("candidate source inventory is incomplete") from exc
        return DaySettlementAssembly(
            active_date=active_date,
            source_namespace=source_namespace,
            source_digest=source_digest,
            plan=plan,
            candidate_selection=candidate_selection,
            completed_batch_count=len(batch_rows),
            active_event_count=len(day_events),
            eligible_thread_count=len(histories),
            projected_thread_count=len(prompt_threads),
            eligible_prior_event_count=len(prior_events),
            projected_prior_event_count=len(projected_prior_events),
            candidate_inventory=inventory,
            assembler_version=assembler_version,
        )
