"""Recognize an existing accepted edge after a lone event becomes a thread.

This retires duplicate work, not evidence. No new semantic judgment is made and
the original source proof must still validate under its original contract.
"""
import json

from .continuation_inventory import candidate_source_version
from .models import DaySettlementCandidateDraft


def distinct_occurrence_audit_key(connection, namespace, from_id, to_id):
    proof = candidate_source_version(connection, source_namespace=namespace,
        prompt_version='operator-distinct-occurrences-v1',
        candidate=DaySettlementCandidateDraft('', from_id, from_id, to_id, ''),
        context_event_ids=(from_id, to_id))
    return 'audit-distinct:v1:' + proof


def explicitly_withdrawn_pair(connection, row, namespace):
    """Only a source-bound operator correction, never an incidental repair status."""
    records = connection.execute(
        "SELECT s.source_key FROM event_links l JOIN thread_events a ON a.event_id=l.from_event_id "
        "JOIN thread_events b ON b.thread_id=a.thread_id AND b.event_id=l.to_event_id "
        "JOIN event_threads t ON t.id=a.thread_id JOIN event_thread_status_log s ON s.thread_id=t.id "
        "WHERE l.from_event_id=? AND l.to_event_id=? AND l.link_type='continues' "
        "AND t.namespace=? AND t.thread_type='scene_continuation' "
        "AND (SELECT x.status FROM event_thread_status_log x WHERE x.thread_id=t.id "
        "ORDER BY x.created_at DESC,x.id DESC LIMIT 1) IN ('retracted','superseded') "
        "AND s.status IN ('retracted','superseded') AND s.reason_code IN "
        "('audited_different_occurrences','audited_occurrence_unresolved')",
        (row['from_event_id'], row['to_event_id'], namespace)).fetchall()
    if not records:
        return False
    try:
        key = distinct_occurrence_audit_key(connection, namespace, row['from_event_id'], row['to_event_id'])
    except ValueError:
        return False
    return any(record[0] == key for record in records)


def already_materialized(connection, receipt, namespace):
    from .continuation_replan import reusable_judgment
    from .store import MemoryV2Store

    candidate = receipt.candidate
    if not candidate.prior_thread_id:
        return False
    a = MemoryV2Store._continuity_membership(connection, candidate.from_event_id, namespace, "scene_continuation")
    b = MemoryV2Store._continuity_membership(connection, candidate.to_event_id, namespace, "scene_continuation")
    if (len(a) != 1 or len(b) != 1 or a[0]["thread_id"] != candidate.prior_thread_id
            or b[0]["thread_id"] != candidate.prior_thread_id
            or a[0]["sequence_no"] >= b[0]["sequence_no"]):
        return False
    rows = connection.execute(
        "SELECT c.*,s.prompt_version FROM day_continuation_candidates c "
        "JOIN day_settlements s ON s.id=c.settlement_id "
        "WHERE s.source_namespace=? AND c.from_event_id=? AND c.match_event_id=? "
        "AND c.to_event_id=? AND COALESCE(c.prior_thread_id,'')=''",
        (namespace, candidate.from_event_id, candidate.match_event_id, candidate.to_event_id)).fetchall()
    for row in rows:
        context = tuple(json.loads(row["context_event_ids_json"]))
        if context != tuple(receipt.context_event_ids):
            continue
        old_candidate = DaySettlementCandidateDraft(row["candidate_ref"], row["from_event_id"],
            row["match_event_id"], row["to_event_id"], "")
        try:
            proof = candidate_source_version(connection, source_namespace=namespace,
                prompt_version=row["prompt_version"], candidate=old_candidate, context_event_ids=context)
        except ValueError:
            continue
        if proof != row["source_version_digest"]:
            continue
        from types import SimpleNamespace
        old = reusable_judgment(connection, SimpleNamespace(candidate=old_candidate,
            source_version_digest=proof), namespace)
        if old is None or old["outcome"] != "continue" or not old["accepted_link_id"]:
            continue
        link = connection.execute("SELECT from_event_id,to_event_id,link_type FROM event_links WHERE id=?",
                                  (old["accepted_link_id"],)).fetchone()
        if link and tuple(link) == (candidate.from_event_id, candidate.to_event_id, "continues"):
            return True
    return False
