"""Rebuild only continuation evidence; historical day compacts stay immutable."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

from .continuation_inventory import candidate_source_version, persist_inventory, validate_inventory
from .day_settlement_lineage import current_day_source_digest
from .day_settlement_runner import day_settlement_prompt_version


def revision_tables(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE name='continuation_revisions' AND type='table'").fetchone() is not None


def latest_revision(connection, settlement_id):
    if not revision_tables(connection):
        return None
    return connection.execute("SELECT * FROM continuation_revisions WHERE settlement_id=? ORDER BY revision_no DESC LIMIT 1",
                              (settlement_id,)).fetchone()


def active_candidate_filter(connection, settlement_id, alias="c"):
    revision = latest_revision(connection, settlement_id)
    if revision is None:
        return "1", ()
    return (f"EXISTS(SELECT 1 FROM continuation_revision_candidates rc WHERE rc.settlement_id={alias}.settlement_id "
            f"AND rc.candidate_ref={alias}.candidate_ref AND rc.revision_id=?)", (revision["id"],))


def current_header(connection, settlement):
    header = dict(settlement)
    revision = latest_revision(connection, settlement["id"])
    if revision:
        header.update({name: revision[name] for name in ("source_digest", "assembler_version", "prompt_version")})
        header["revision_id"] = revision["id"]
    return header


def needs_replan(connection, settlement):
    header = current_header(connection, settlement)
    digest, _, _ = current_day_source_digest(connection, header["source_namespace"], header["active_date"],
                                            assembler_version=header["assembler_version"])
    if digest != header["source_digest"]:
        return True
    clause, args = active_candidate_filter(connection, settlement["id"])
    rows = connection.execute("SELECT c.* FROM day_continuation_candidates c WHERE c.settlement_id=? AND " + clause,
                              (settlement["id"], *args)).fetchall()
    from .models import DaySettlementCandidateDraft
    for row in rows:
        candidate = DaySettlementCandidateDraft(row["candidate_ref"], row["from_event_id"], row["match_event_id"],
                                                row["to_event_id"], row["prior_thread_id"] or "")
        try:
            digest = candidate_source_version(connection, source_namespace=header["source_namespace"],
                prompt_version=header["prompt_version"], candidate=candidate,
                context_event_ids=tuple(json.loads(row["context_event_ids_json"])))
        except ValueError:
            return True
        if digest != row["source_version_digest"]:
            return True
    # Source text can remain unchanged while another accepted edge moves the
    # append endpoint. Only unjudged pairs need refreshing: an already recorded
    # separate/unknown judgment remains valid when the graph later grows.
    from .continuation_pages import pending_rows
    from .continuation_page_store import candidate_graph_current
    from .materialized_continuation import already_materialized
    pending = pending_rows(connection, settlement["id"])
    if any(not candidate_graph_current(connection, row, header["source_namespace"])
           for row in pending):
        return True
    for row in pending:
        receipt = SimpleNamespace(candidate=DaySettlementCandidateDraft(row["candidate_ref"], row["from_event_id"],
            row["match_event_id"], row["to_event_id"], row["prior_thread_id"] or ""),
            context_event_ids=tuple(json.loads(row["context_event_ids_json"])))
        if already_materialized(connection, receipt, header["source_namespace"]):
            return True
    active_refs = {row["candidate_ref"] for row in rows}
    if active_refs:
        blocked = connection.execute("SELECT r.candidate_refs_json FROM continuation_page_requests r "
            "JOIN continuation_page_blocks b ON b.page_id=r.id WHERE r.settlement_id=?", (settlement["id"],)).fetchall()
        return any(active_refs.intersection(json.loads(row[0])) for row in blocked)
    return False


def replan_dates(connection, namespace, cutoff):
    if not revision_tables(connection):
        return ()
    rows = connection.execute("SELECT * FROM day_settlements WHERE source_namespace=? AND active_date<? "
                              "ORDER BY active_date", (namespace, cutoff)).fetchall()
    return tuple(row["active_date"] for row in rows
                 if row["active_date"] >= "2026-10-07"
                 and row["prompt_version"] == day_settlement_prompt_version(row["active_date"])
                 and needs_replan(connection, row))


def work_digest(connection, namespace, active_date):
    """Retry identity includes historical evidence and live graph, not failure counters."""
    settlement = connection.execute("SELECT * FROM day_settlements WHERE source_namespace=? AND active_date=? "
        "AND prompt_version=? ORDER BY created_at DESC,id DESC LIMIT 1",
        (namespace, active_date, day_settlement_prompt_version(active_date))).fetchone()
    if settlement is None:
        return ""
    header = current_header(connection, settlement)
    day_digest, _, _ = current_day_source_digest(connection, namespace, active_date, assembler_version=header["assembler_version"])
    clause, args = active_candidate_filter(connection, settlement["id"])
    rows = connection.execute("SELECT c.* FROM day_continuation_candidates c WHERE c.settlement_id=? AND " + clause + " ORDER BY c.candidate_order",
                              (settlement["id"], *args)).fetchall()
    from .models import DaySettlementCandidateDraft
    from .store import MemoryV2Store
    proofs = []
    for row in rows:
        candidate = DaySettlementCandidateDraft(row["candidate_ref"], row["from_event_id"], row["match_event_id"], row["to_event_id"], row["prior_thread_id"] or "")
        context = tuple(json.loads(row["context_event_ids_json"]))
        try:
            proof = candidate_source_version(connection, source_namespace=namespace, prompt_version=header["prompt_version"],
                                             candidate=candidate, context_event_ids=context)
        except ValueError:
            proof = [(event_id, connection.execute("SELECT id FROM event_status_log WHERE event_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                                                  (event_id,)).fetchone()) for event_id in context]
            proof = [(event_id, status[0] if status else "") for event_id, status in proof]
            if candidate.prior_thread_id:
                status = connection.execute("SELECT id FROM event_thread_status_log WHERE thread_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                                            (candidate.prior_thread_id,)).fetchone()
                proof.append((candidate.prior_thread_id, status[0] if status else ""))
        graph = []
        for event_id in (candidate.from_event_id, candidate.to_event_id):
            for membership in MemoryV2Store._continuity_membership(connection, event_id, namespace, "scene_continuation"):
                tail = connection.execute("SELECT event_id FROM thread_events WHERE thread_id=? ORDER BY sequence_no DESC LIMIT 1",
                                          (membership["thread_id"],)).fetchone()[0]
                graph.append((event_id, membership["thread_id"], membership["sequence_no"], tail))
        proofs.append((row["candidate_ref"], proof, graph))
    body = [header.get("revision_id", ""), day_digest, proofs]
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuation_page_recovered_outputs'").fetchone():
        recovered = [tuple(row) for row in connection.execute(
            'SELECT o.page_id,o.output_json FROM continuation_page_recovered_outputs o '
            'JOIN continuation_page_requests r ON r.id=o.page_id WHERE r.settlement_id=? ORDER BY o.page_id',
            (settlement['id'],))]
        if recovered:
            # First judgment has progressed; its old parser failure cannot exhaust
            # the independent review's first attempt. Unknown calls gain no new identity.
            body.append(['revalidated_initial_outputs', recovered])
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reusable_judgment(connection, receipt, namespace):
    """Exact pair proof and compatible contract only; IDs or wording alone cannot authorize reuse."""
    from .continuation_pages import COMPATIBLE_PAGE_PROMPT_VERSIONS
    from .continuation_raw_checkpoint import output_relation
    from .day_settlement_runner import (CONTINUITY_DAY_PROMPT_VERSION, EXPERIENCE_DAY_PROMPT_VERSION,
                                        CANDIDATE_EVIDENCE_PROMPT_VERSION, SOURCE_CERTAINTY_PROMPT_VERSION,
                                        ACTION_STATUS_PROMPT_VERSION)
    placeholders = ','.join('?' for _ in COMPATIBLE_PAGE_PROMPT_VERSIONS)
    rows = connection.execute(
        "SELECT c.settlement_id,c.candidate_ref,d.outcome,d.confidence,d.reason_codes_json,d.accepted_link_id,'day' AS origin_kind,NULL AS origin_page_id "
        "FROM day_continuation_candidates c JOIN day_thread_judgments d ON d.settlement_id=c.settlement_id AND d.candidate_ref=c.candidate_ref "
        "JOIN day_settlements s ON s.id=c.settlement_id WHERE c.source_version_digest=? AND s.prompt_version IN (?,?,?,?,?) "
        "UNION ALL SELECT c.settlement_id,c.candidate_ref,p.outcome,p.confidence,p.reason_codes_json,p.accepted_link_id,'page',p.page_id "
        "FROM day_continuation_candidates c JOIN continuation_page_judgments p ON p.settlement_id=c.settlement_id AND p.candidate_ref=c.candidate_ref "
        f"JOIN continuation_page_requests r ON r.id=p.page_id WHERE c.source_version_digest=? AND r.prompt_version IN ({placeholders}) "
        "UNION ALL SELECT c.settlement_id,c.candidate_ref,json_extract(j.value,'$.outcome'),json_extract(j.value,'$.confidence'), "
        f"json_extract(j.value,'$.reason_codes'),NULL,'validated_output',r.id FROM {output_relation(connection)} o "
        "JOIN continuation_page_requests r ON r.id=o.page_id JOIN json_each(CASE WHEN o.status='validated' THEN o.output_json ELSE '{}' END,'$.thread_judgments') j "
        "JOIN day_continuation_candidates c ON c.settlement_id=r.settlement_id AND c.candidate_ref=json_extract(j.value,'$.candidate_ref') "
        f"WHERE o.status='validated' AND c.source_version_digest=? AND r.prompt_version IN ({placeholders}) "
        "AND json_extract(j.value,'$.outcome') IN ('separate','unknown') "
        "AND NOT EXISTS(SELECT 1 FROM continuation_page_commits k WHERE k.page_id=r.id)",
        (receipt.source_version_digest, CONTINUITY_DAY_PROMPT_VERSION, EXPERIENCE_DAY_PROMPT_VERSION,
         CANDIDATE_EVIDENCE_PROMPT_VERSION, SOURCE_CERTAINTY_PROMPT_VERSION, ACTION_STATUS_PROMPT_VERSION,
         receipt.source_version_digest, *COMPATIBLE_PAGE_PROMPT_VERSIONS,
         receipt.source_version_digest, *COMPATIBLE_PAGE_PROMPT_VERSIONS),
    ).fetchall()
    # Conflicting prior decisions do not gain certainty through recycling.
    signatures = {(row["outcome"], row["confidence"], tuple(sorted(json.loads(row["reason_codes_json"])))) for row in rows}
    if len(signatures) != 1:
        return None
    result = rows[0]
    from .day_settlement import MIN_CONTINUATION_CONFIDENCE
    if result["outcome"] == "continue" and result["confidence"] >= MIN_CONTINUATION_CONFIDENCE:
        from .store import MemoryV2Store
        a = MemoryV2Store._continuity_membership(connection, receipt.candidate.from_event_id, namespace, "scene_continuation")
        b = MemoryV2Store._continuity_membership(connection, receipt.candidate.to_event_id, namespace, "scene_continuation")
        if not result["accepted_link_id"] or len(a) != 1 or len(b) != 1 or a[0]["thread_id"] != b[0]["thread_id"] or a[0]["sequence_no"] >= b[0]["sequence_no"]:
            return None
    return result


def persist_replan(connection, settlement_id, assembly, now):
    from .continuation_pages import PAGE_PROMPT_VERSION
    settlement = connection.execute("SELECT * FROM day_settlements WHERE id=?", (settlement_id,)).fetchone()
    if settlement is None or (settlement["active_date"], settlement["source_namespace"]) != (assembly.active_date, assembly.source_namespace):
        raise ValueError("replan_settlement_identity_changed")
    prompt = day_settlement_prompt_version(assembly.active_date)
    digest, _, _ = current_day_source_digest(connection, assembly.source_namespace, assembly.active_date,
                                            assembler_version=assembly.assembler_version)
    if digest != assembly.source_digest:
        raise ValueError("replan_day_sources_changed")
    validate_inventory(connection, SimpleNamespace(source_namespace=assembly.source_namespace,
        prompt_version=prompt, candidate_inventory=assembly.candidate_inventory))
    from .continuation_page_store import candidate_graph_current
    from .materialized_continuation import already_materialized
    from dataclasses import asdict
    # The as-of-day assembler may nominate a historical endpoint which is no
    # longer appendable today. Preserve known decisions, but retire unjudged
    # executable pairings rather than repeatedly freezing the same stale graph.
    # This changes candidate membership only, not event facts or semantic outcomes.
    materialized = tuple(item for item in assembly.candidate_inventory
                         if already_materialized(connection, item, assembly.source_namespace))
    inventory = tuple(item for item in assembly.candidate_inventory if item not in materialized
                      and (candidate_graph_current(connection, asdict(item.candidate), assembly.source_namespace)
                      or reusable_judgment(connection, item, assembly.source_namespace) is not None))
    retired = len(assembly.candidate_inventory) - len(inventory)
    body = {"source": digest, "assembler": assembly.assembler_version, "prompt": prompt, "page_prompt": PAGE_PROMPT_VERSION,
            "candidates": [(item.candidate, item.source_version_digest, item.selection_state,
                            item.selector_version, item.local_score, item.signal_codes, item.context_event_ids)
                           for item in inventory]}
    from dataclasses import asdict, is_dataclass
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True,
        default=lambda obj: asdict(obj) if is_dataclass(obj) else obj, separators=(",", ":")).encode()).hexdigest()
    previous = latest_revision(connection, settlement_id)
    if previous and previous["input_digest"] == fingerprint:
        return {"status": "cached", "revision_id": previous["id"], "candidate_count": len(inventory),
                "retired_candidate_count": retired, "already_materialized_count": len(materialized), "reused_count": 0}
    number = previous["revision_no"] + 1 if previous else 1
    revision_id = "continuation_revision_" + hashlib.sha256(f"{settlement_id}\0{number}\0{fingerprint}".encode()).hexdigest()[:24]
    connection.execute("INSERT INTO continuation_revisions VALUES (?,?,?,?,?,?,?,?,?)",
        (revision_id, settlement_id, number, digest, assembly.assembler_version, prompt, PAGE_PROMPT_VERSION, fingerprint, now))
    inventory = tuple(replace(item, candidate=replace(item.candidate, candidate_ref="r" + hashlib.sha256(
                           f"{revision_id}\0{item.candidate.candidate_ref}".encode()).hexdigest()[:20])) for item in inventory)
    order_start = connection.execute("SELECT COALESCE(MAX(candidate_order),-1)+1 FROM day_continuation_candidates WHERE settlement_id=?",
                                     (settlement_id,)).fetchone()[0]
    persist_inventory(connection, SimpleNamespace(settlement_id=settlement_id, candidate_inventory=inventory), now, order_start=order_start)
    reused = 0
    for item in inventory:
        connection.execute("INSERT INTO continuation_revision_candidates VALUES (?,?,?)",
                           (revision_id, settlement_id, item.candidate.candidate_ref))
        old = reusable_judgment(connection, item, assembly.source_namespace)
        if old:
            connection.execute("INSERT INTO continuation_judgment_reuses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (revision_id, settlement_id, item.candidate.candidate_ref, old["settlement_id"], old["candidate_ref"],
                 old["origin_kind"], old["origin_page_id"],
                 old["outcome"], old["confidence"], old["reason_codes_json"], old["accepted_link_id"], now))
            reused += 1
    return {"status": "committed", "revision_id": revision_id, "candidate_count": len(inventory),
            "retired_candidate_count": retired, "already_materialized_count": len(materialized), "reused_count": reused}


def prepare_continuation_replan(store, *, active_date, completed_before_active_date, source_namespace="mainline", upgrade_inventory=False):
    """No encoding or LLM; transaction checks the frozen evidence again."""
    from .day_settlement import DAY_SETTLEMENT_VERSION
    from .day_settlement_assembler import DaySettlementPlanAssembler
    receipt = store.get_day_settlement_receipt_for_day(source_namespace=source_namespace, active_date=active_date,
        settlement_version=DAY_SETTLEMENT_VERSION, prompt_version=day_settlement_prompt_version(active_date))
    if receipt is None:
        return {"status": "waiting_day_settlement", "candidate_count": 0, "reused_count": 0}
    from contextlib import closing
    from .day_settlement_candidates import DaySettlementCandidatePolicy
    from .settlement_candidate_pool import ASSEMBLER_VERSION as POOL_VERSION
    policy = DaySettlementCandidatePolicy.for_active_date(active_date)
    with closing(store._connect()) as connection:
        header = current_header(connection, receipt)
    if upgrade_inventory or header.get('assembler_version') == POOL_VERSION:
        policy = replace(policy, full_history_inventory=True)
    assembly = DaySettlementPlanAssembler(store.db_path).assemble(active_date=active_date,
        completed_before_active_date=completed_before_active_date, source_namespace=source_namespace,
        candidate_policy=policy)
    return store.persist_continuation_replan(receipt["id"], assembly)
