"""Source-versioned continuation inventory; no model calls or source bodies.

Budget-deferred candidates and omitted judgments are not semantic rejections.
This module freezes their identities and validates receipt reuse against the
same event evidence and thread projection, rather than a mutable day-wide ID.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict

from .day_settlement import DaySettlementPlan, SettlementContinuationCandidate
from .day_settlement_lineage import (
    effective_event_status_sql, effective_event_thread_status_sql, has_event_thread_status_log,
)
from .models import ContinuationCandidateReceiptDraft, DaySettlementCandidateDraft


INVENTORY_VERSION = "continuation-inventory-v1"


class ContinuationInventoryError(ValueError):
    """A frozen candidate's supporting sources are no longer available."""


def candidate_identity(
    plan: DaySettlementPlan, candidate: SettlementContinuationCandidate,
) -> tuple[DaySettlementCandidateDraft, tuple[str, ...]]:
    events = {event.ref: event for event in (*plan.day_events, *plan.prior_events)}
    thread_id = ""
    if candidate.prior_thread_ref:
        thread = next(thread for thread in plan.prior_threads if thread.ref == candidate.prior_thread_ref)
        match = next(event for event in thread.representative_events if event.ref == candidate.match_event_ref)
        start = thread.representative_events[-1]
        from .settlement_candidate_pool import CandidatePool
        nodes = plan.candidate_nodes(candidate) if isinstance(plan, CandidatePool) else thread.representative_events
        context = [event.event_id for event in nodes]
        thread_id = thread.thread_id
    else:
        match = start = events[candidate.match_event_ref]
        context = [match.event_id]
    target = events[candidate.to_day_event_ref]
    context.append(target.event_id)
    return DaySettlementCandidateDraft(
        candidate.ref, start.event_id, match.event_id, target.event_id, thread_id,
    ), tuple(dict.fromkeys(context))


def candidate_source_version(
    connection: sqlite3.Connection, *, source_namespace: str, prompt_version: str,
    candidate: DaySettlementCandidateDraft, context_event_ids: tuple[str, ...],
) -> str:
    if not context_event_ids or len(context_event_ids) != len(set(context_event_ids)) or not {
        candidate.from_event_id, candidate.match_event_id, candidate.to_event_id,
    }.issubset(context_event_ids):
        raise ContinuationInventoryError("candidate context identity is invalid")
    records = []
    for event_id in context_event_ids:
        event = connection.execute(
            "SELECT e.id, e.batch_id, e.content_digest, e.summary, e.reported_at, "
            "e.event_type, e.subject_id, e.attributes_json, b.source_digest, "
            "b.source_namespace, b.status, "
            f"{effective_event_status_sql('e')} AS current_status "
            "FROM events e JOIN encoding_batches b ON b.id=e.batch_id WHERE e.id=?",
            (event_id,),
        ).fetchone()
        if event is None or event["source_namespace"] != source_namespace or (
            event["status"] != "completed" or event["current_status"] != "active"
        ):
            raise ContinuationInventoryError("candidate event source is not active")
        sources = connection.execute(
            "SELECT es.*, bs.content_digest AS message_content_digest FROM event_sources es "
            "JOIN encoding_batch_sources bs ON bs.batch_id=? AND bs.message_id=es.message_id "
            "AND bs.message_row_id=es.message_row_id WHERE es.event_id=? ORDER BY es.source_order",
            (event["batch_id"], event_id),
        ).fetchall()
        source_count = connection.execute(
            "SELECT COUNT(*) FROM event_sources WHERE event_id=?", (event_id,),
        ).fetchone()[0]
        if not sources or len(sources) != source_count:
            raise ContinuationInventoryError("candidate source manifest is incomplete")
        participants = [row[0] for row in connection.execute(
            "SELECT participant_id FROM event_participants WHERE event_id=? ORDER BY participant_order",
            (event_id,),
        )]
        records.append({"event": dict(event), "sources": [dict(row) for row in sources],
                        "participants": participants})
    thread_nodes = []
    if candidate.prior_thread_id:
        thread = connection.execute(
            "SELECT t.id, t.namespace, "
            f"{effective_event_thread_status_sql('t', status_log_available=has_event_thread_status_log(connection))} AS current_status "
            "FROM event_threads t WHERE t.id=?", (candidate.prior_thread_id,),
        ).fetchone()
        if thread is None or thread["namespace"] != source_namespace or thread["current_status"] != "active":
            raise ContinuationInventoryError("candidate thread source is not active")
        tail = connection.execute(
            "SELECT sequence_no FROM thread_events WHERE thread_id=? AND event_id=?",
            (candidate.prior_thread_id, candidate.from_event_id),
        ).fetchone()
        if tail is None:
            raise ContinuationInventoryError("candidate append source left its thread")
        # The frozen historical prefix is evidence. Later appends (including
        # this receipt's own result) do not rewrite that evidence. Live-tail
        # execution admission remains the store's separate responsibility.
        thread_nodes = [list(row) for row in connection.execute(
            "SELECT event_id, sequence_no FROM thread_events WHERE thread_id=? "
            "AND sequence_no<=? ORDER BY sequence_no", (candidate.prior_thread_id, tail[0]),
        )]
    body = {"version": INVENTORY_VERSION, "namespace": source_namespace,
            "prompt_version": prompt_version, "from": candidate.from_event_id,
            "match": candidate.match_event_id, "to": candidate.to_event_id,
            "thread": candidate.prior_thread_id, "thread_nodes": thread_nodes, "context": records}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def freeze_candidate_inventory(connection, *, plan, selection, prompt_version):
    result = []
    for state, candidates in (("in_budget", selection.candidates),
                              ("out_of_budget", selection.deferred_candidates)):
        for candidate in candidates:
            identity, context = candidate_identity(plan, candidate)
            result.append(ContinuationCandidateReceiptDraft(
                candidate=identity, selection_state=state, selector_version=selection.selector_version,
                local_score=candidate.local_score, signal_codes=tuple(candidate.signal_codes),
                source_version_digest=candidate_source_version(
                    connection, source_namespace=plan.source_namespace, prompt_version=prompt_version,
                    candidate=identity, context_event_ids=context,
                ), context_event_ids=context,
            ))
    return tuple(result)


def validate_inventory(connection, settlement):
    """Recheck every frozen pair before any accepted append changes its thread."""
    for receipt in settlement.candidate_inventory:
        current = candidate_source_version(
            connection, source_namespace=settlement.source_namespace,
            prompt_version=settlement.prompt_version, candidate=receipt.candidate,
            context_event_ids=tuple(receipt.context_event_ids),
        )
        if current != receipt.source_version_digest:
            raise ContinuationInventoryError("candidate sources changed after assembly")


def persist_inventory(connection, settlement, now, *, order_start=0):
    for order, receipt in enumerate(settlement.candidate_inventory, start=order_start):
        item = receipt.candidate
        connection.execute(
            "INSERT INTO day_continuation_candidates "
            "(settlement_id,candidate_ref,candidate_order,from_event_id,match_event_id,to_event_id,"
            "prior_thread_id,selection_state,selector_version,local_score,signal_codes_json,"
            "source_version_digest,context_event_ids_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (settlement.settlement_id, item.candidate_ref, order, item.from_event_id,
             item.match_event_id, item.to_event_id, item.prior_thread_id or None,
             receipt.selection_state, receipt.selector_version, receipt.local_score,
             json.dumps(list(receipt.signal_codes)), receipt.source_version_digest,
             json.dumps(list(receipt.context_event_ids)), now),
        )


def inventory_payload(inventory):
    return [asdict(receipt) for receipt in inventory]


def inventory_counts(connection, settlement_id):
    from .continuation_replan import active_candidate_filter, latest_revision, revision_tables
    active_clause, active_args = active_candidate_filter(connection, settlement_id)
    receipt = connection.execute(
        "SELECT candidate_count,prompt_version FROM day_settlements WHERE id=?", (settlement_id,),
    ).fetchone()
    has_pages = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuation_page_judgments'").fetchone() is not None
    page_join = (" LEFT JOIN continuation_page_judgments p ON p.settlement_id=c.settlement_id AND p.candidate_ref=c.candidate_ref "
                 if has_pages else " ")
    no_judgment = "j.id IS NULL" + (" AND p.candidate_ref IS NULL" if has_pages else "")
    if revision_tables(connection):
        page_join += " LEFT JOIN continuation_judgment_reuses u ON u.settlement_id=c.settlement_id AND u.candidate_ref=c.candidate_ref "
        no_judgment += " AND u.candidate_ref IS NULL"
    counts = connection.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(c.selection_state='in_budget'),0) AS selected, "
        f"COALESCE(SUM(c.selection_state='out_of_budget' AND {no_judgment}),0) AS deferred, "
        f"COALESCE(SUM(c.selection_state='in_budget' AND {no_judgment}),0) AS unjudged, "
        f"COALESCE(SUM({no_judgment}),0) AS remaining "
        "FROM day_continuation_candidates c LEFT JOIN day_thread_judgments j "
        "ON j.settlement_id=c.settlement_id AND j.candidate_ref=c.candidate_ref " + page_join + "WHERE c.settlement_id=? AND " + active_clause,
        (settlement_id, *active_args),
    ).fetchone()
    from .day_settlement_runner import prompt_has_continuation_inventory
    recorded = bool(latest_revision(connection, settlement_id)) or bool(receipt and prompt_has_continuation_inventory(receipt["prompt_version"])
                    and receipt["candidate_count"] == counts["selected"])
    return {"inventory_recorded": recorded, "deferred_candidate_count": int(counts["deferred"]),
            "unjudged_candidate_count": int(counts["unjudged"]), "remaining_candidate_count": int(counts["remaining"])}


def read_inventory(connection, settlement_id):
    from .continuation_replan import active_candidate_filter, revision_tables
    active_clause, active_args = active_candidate_filter(connection, settlement_id)
    has_pages = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuation_page_judgments'").fetchone() is not None
    page_join = (" LEFT JOIN continuation_page_judgments p ON p.settlement_id=c.settlement_id AND p.candidate_ref=c.candidate_ref "
                 if has_pages else " ")
    outcome = "COALESCE(j.outcome,p.outcome)" if has_pages else "j.outcome"
    confidence = "COALESCE(j.confidence,p.confidence)" if has_pages else "j.confidence"
    if revision_tables(connection):
        page_join += " LEFT JOIN continuation_judgment_reuses u ON u.settlement_id=c.settlement_id AND u.candidate_ref=c.candidate_ref "
        outcome = "COALESCE(j.outcome,p.outcome,u.outcome)"
        confidence = "COALESCE(j.confidence,p.confidence,u.confidence)"
    rows = connection.execute(
        f"SELECT c.*, {outcome} AS outcome, {confidence} AS confidence FROM day_continuation_candidates c "
        "LEFT JOIN day_thread_judgments j ON j.settlement_id=c.settlement_id "
        "AND j.candidate_ref=c.candidate_ref " + page_join + "WHERE c.settlement_id=? AND " + active_clause + " ORDER BY c.candidate_order",
        (settlement_id, *active_args),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["signal_codes"] = json.loads(item.pop("signal_codes_json"))
        item["context_event_ids"] = json.loads(item.pop("context_event_ids_json"))
        item["judgment_state"] = item["outcome"] or (
            "deferred" if item["selection_state"] == "out_of_budget" else "unjudged")
        result.append(item)
    return result


def receipt_sources_current(connection, settlement_id):
    """Historical evidence can remain valid after appends, never after mutation."""
    settlement = connection.execute(
        "SELECT source_namespace,prompt_version,candidate_count FROM day_settlements WHERE id=?",
        (settlement_id,),
    ).fetchone()
    if settlement is None:
        return False
    selected_count = 0
    for row in connection.execute(
        "SELECT * FROM day_continuation_candidates WHERE settlement_id=?", (settlement_id,),
    ):
        selected_count += row["selection_state"] == "in_budget"
        candidate = DaySettlementCandidateDraft(row["candidate_ref"], row["from_event_id"],
            row["match_event_id"], row["to_event_id"], row["prior_thread_id"] or "")
        kwargs = dict(source_namespace=settlement["source_namespace"],
                      prompt_version=settlement["prompt_version"], candidate=candidate,
                      context_event_ids=tuple(json.loads(row["context_event_ids_json"])))
        try:
            current = candidate_source_version(connection, **kwargs)
            if current != row["source_version_digest"]:
                return False
        except ContinuationInventoryError:
            return False
    return selected_count == settlement["candidate_count"]
