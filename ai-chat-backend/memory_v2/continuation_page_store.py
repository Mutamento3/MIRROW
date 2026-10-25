"""Append-only page receipts and atomic graph application inside MemoryV2Store."""
import json
from .continuation_pages import (
    PAGE_PROMPT_VERSION, COMPATIBLE_PAGE_PROMPT_VERSIONS, pending_rows, project_page, tables_available, judgments_payload,
)
from .continuation_inventory import validate_inventory
from .day_settlement_lineage import current_day_source_digest
from .day_settlement import MIN_CONTINUATION_CONFIDENCE
from .models import EventLinkDraft
from .continuation_raw_checkpoint import output_relation


def settlement_row(connection, settlement_id):
    row = connection.execute("SELECT * FROM day_settlements WHERE id=?", (settlement_id,)).fetchone()
    if row is None:
        raise ValueError("continuation_settlement_unavailable")
    from .continuation_replan import current_header
    row = current_header(connection, row)
    digest, _, _ = current_day_source_digest(connection, row["source_namespace"], row["active_date"],
                                            assembler_version=row["assembler_version"])
    if digest != row["source_digest"]:
        raise ValueError("continuation_day_sources_changed")
    return row


def candidate_graph_current(connection, row, namespace):
    """Frozen evidence may be intact while its linear append position has moved."""
    from .store import MemoryV2Store
    times = connection.execute('SELECT id,reported_at FROM events WHERE id IN (?,?)',
        (row['from_event_id'], row['to_event_id'])).fetchall()
    from .source_time import instant
    by_id = {item['id']: instant(item['reported_at']) for item in times}
    if (len(by_id) != 2 or by_id[row['from_event_id']] > by_id[row['to_event_id']]):
        return False
    source = MemoryV2Store._continuity_membership(connection, row["from_event_id"], namespace, "scene_continuation")
    target = MemoryV2Store._continuity_membership(connection, row["to_event_id"], namespace, "scene_continuation")
    if len(source) > 1 or len(target) > 1:
        return False
    # An explicitly withdrawn historical edge cannot be resurrected by another
    # paid judgment. Its immutable link and thread still exist, but no longer
    # authorize execution. New evidence must use the correction/projection path.
    from .materialized_continuation import explicitly_withdrawn_pair
    if explicitly_withdrawn_pair(connection, row, namespace):
        return False
    if target:
        if source and source[0]["thread_id"] != target[0]["thread_id"]:
            from .thread_join import joinable_nodes
            return bool(joinable_nodes(connection, source, target, namespace))
        return bool(source and source[0]["thread_id"] == target[0]["thread_id"]
                    and source[0]["sequence_no"] < target[0]["sequence_no"])
    if source:
        tail = connection.execute("SELECT MAX(sequence_no) FROM thread_events WHERE thread_id=?",
                                  (source[0]["thread_id"],)).fetchone()[0]
        return source[0]["sequence_no"] == tail
    return True


def load_page(connection, settlement_id, explicit_retry):
    if not tables_available(connection):
        return None, None, 0, False
    settlement = settlement_row(connection, settlement_id)
    pending = pending_rows(connection, settlement_id)
    from .continuation_replan import active_candidate_filter
    clause, args = active_candidate_filter(connection, settlement_id)
    active_refs = {row[0] for row in connection.execute(
        "SELECT c.candidate_ref FROM day_continuation_candidates c WHERE c.settlement_id=? AND " + clause,
        (settlement_id, *args))}
    # Already-paid validated output has priority, including after a crash
    # between output persistence and the graph transaction.
    saved_rows = connection.execute(
        "SELECT r.*,o.output_json FROM continuation_page_requests r "
        f"JOIN {output_relation(connection)} o ON o.page_id=r.id "
        "LEFT JOIN continuation_page_commits c ON c.page_id=r.id "
        "LEFT JOIN continuation_page_blocks b ON b.page_id=r.id "
        "WHERE r.settlement_id=? AND o.status='validated' AND c.page_id IS NULL AND b.page_id IS NULL ORDER BY r.created_at,r.id",
        (settlement_id,),
    ).fetchall()
    saved = next((row for row in saved_rows if set(json.loads(row["candidate_refs_json"])).issubset(active_refs)), None)
    if saved:
        refs = json.loads(saved["candidate_refs_json"])
        by_ref = {row["candidate_ref"]: row for row in connection.execute(
            "SELECT * FROM day_continuation_candidates WHERE settlement_id=?", (settlement_id,),
        )}
        page = project_page(connection, settlement, [by_ref[ref] for ref in refs],
                            page_id=saved["id"], attempt_no=saved["attempt_no"], prompt_version=saved["prompt_version"])
        if page.input_digest != saved["input_digest"] or saved["prompt_version"] not in COMPATIBLE_PAGE_PROMPT_VERSIONS:
            raise ValueError("continuation_page_input_changed")
        return page, json.loads(saved["output_json"]), len(pending), False
    if not pending:
        return None, None, 0, False
    requests = connection.execute(
        "SELECT candidate_refs_json FROM continuation_page_requests WHERE settlement_id=?", (settlement_id,),
    ).fetchall()
    attempted = {ref for request in requests for ref in json.loads(request[0])}
    # A revision renames candidate refs, not their evidence. Renaming an
    # unchanged paid/omitted pair must not silently authorize another call.
    attempted_versions = {row[0] for row in connection.execute(
        "SELECT c.source_version_digest FROM continuation_page_requests r "
        "JOIN json_each(r.candidate_refs_json) j "
        "JOIN day_continuation_candidates c ON c.settlement_id=r.settlement_id AND c.candidate_ref=j.value "
        "WHERE r.settlement_id=?", (settlement_id,))}
    blocked = {ref for request in connection.execute(
        "SELECT r.candidate_refs_json FROM continuation_page_requests r JOIN continuation_page_blocks b ON b.page_id=r.id "
        "WHERE r.settlement_id=?", (settlement_id,)) for ref in json.loads(request[0])}
    ready = [row for row in pending if row["candidate_ref"] not in blocked
             and (explicit_retry or (row["candidate_ref"] not in attempted
                                     and row["source_version_digest"] not in attempted_versions))
             and candidate_graph_current(connection, row, settlement["source_namespace"])]
    if not ready:
        return None, None, len(pending), True
    from .settlement_candidate_pool import pack_pending_rows
    rows = pack_pending_rows(ready)
    if not rows:
        return None, None, len(pending), True
    page = project_page(connection, settlement, rows)
    attempt_no = 1 + connection.execute(
        "SELECT COUNT(*) FROM continuation_page_requests WHERE settlement_id=? AND input_digest=?",
        (settlement_id, page.input_digest),
    ).fetchone()[0]
    if attempt_no != 1:
        page = project_page(connection, settlement, rows, attempt_no=attempt_no)
    return page, None, len(pending), False


def check_page(connection, page):
    settlement = settlement_row(connection, page.settlement_id)
    if (settlement["source_namespace"] != page.plan.source_namespace
            or settlement["active_date"] != page.plan.active_date
            or settlement["source_digest"] != page.plan.source_digest):
        raise ValueError("continuation_page_identity_changed")
    from types import SimpleNamespace
    validate_inventory(connection, SimpleNamespace(
        source_namespace=settlement["source_namespace"], prompt_version=settlement["prompt_version"],
        candidate_inventory=page.receipts,
    ))
    refs = [item.candidate.candidate_ref for item in page.receipts]
    from .continuation_replan import active_candidate_filter
    clause, args = active_candidate_filter(connection, page.settlement_id)
    active_refs = {row[0] for row in connection.execute(
        "SELECT c.candidate_ref FROM day_continuation_candidates c WHERE c.settlement_id=? AND " + clause,
        (page.settlement_id, *args))}
    if not set(refs).issubset(active_refs):
        raise ValueError("continuation_page_revision_superseded")
    by_ref = {row["candidate_ref"]: row for row in connection.execute(
        "SELECT * FROM day_continuation_candidates WHERE settlement_id=?", (page.settlement_id,),
    )}
    fresh = project_page(connection, settlement, [by_ref[ref] for ref in refs],
                         page_id=page.page_id, attempt_no=page.attempt_no, prompt_version=page.prompt_version)
    if page.prompt_version not in COMPATIBLE_PAGE_PROMPT_VERSIONS:
        raise ValueError("continuation_page_prompt_incompatible")
    if fresh != page:
        raise ValueError("continuation_page_frozen_input_mismatch")


def begin_page(connection, page, now):
    check_page(connection, page)
    if connection.execute("SELECT 1 FROM continuation_page_requests WHERE id=?", (page.page_id,)).fetchone():
        return False
    connection.execute(
        "INSERT INTO continuation_page_requests VALUES (?,?,?,?,?,?,?)",
        (page.page_id, page.settlement_id, page.prompt_version, page.input_digest,
         json.dumps([item.candidate.candidate_ref for item in page.receipts]), page.attempt_no, now),
    )
    return True


def save_output(connection, page, status, output, error_code, now):
    request = connection.execute("SELECT input_digest FROM continuation_page_requests WHERE id=?", (page.page_id,)).fetchone()
    if request is None or request[0] != page.input_digest:
        raise ValueError("continuation_request_unavailable")
    # Only validated judgments are replayable. Rejections retain text-free
    # structural diagnostics, never provider prose or hidden reasoning.
    from .day_settlement import parse_continuation_judgments
    if status == "validated":
        output = judgments_payload(parse_continuation_judgments(page.plan, output))
    elif status not in {"provider_error", "contract_error"}:
        raise ValueError("invalid_continuation_output_state")
    if status == 'contract_error':
        from .continuation_diagnostics import project
        diagnostic = project(output.get('contract_diagnostics') if isinstance(output, dict) else None)
        body = json.dumps({'contract_diagnostics': diagnostic}, sort_keys=True) if diagnostic else ''
    else:
        body = json.dumps(output, ensure_ascii=False, sort_keys=True) if status == "validated" else ""
    connection.execute("INSERT INTO continuation_page_outputs VALUES (?,?,?,?,?)",
                       (page.page_id, status, body, error_code, now))


def seed_day_judgments(connection, settlement_id, output, now):
    """Transfer paid initial judgments into the recovery lane in the day transaction."""
    refs = [row['candidate_ref'] for row in output['thread_judgments']]
    if not refs:
        return
    if len(refs) != len(set(refs)):
        raise ValueError('duplicate_day_seed_candidate')
    settlement = settlement_row(connection, settlement_id)
    by_ref = {row['candidate_ref']: row for row in connection.execute(
        'SELECT * FROM day_continuation_candidates WHERE settlement_id=?', (settlement_id,))}
    if not set(refs) <= set(by_ref):
        raise ValueError('day_seed_candidate_missing')
    page = project_page(connection, settlement, [by_ref[ref] for ref in refs])
    if not begin_page(connection, page, now):
        previous = connection.execute('SELECT output_json FROM continuation_page_outputs WHERE page_id=?',
                                      (page.page_id,)).fetchone()
        if not previous or json.loads(previous[0]) != output:
            raise ValueError('day_seed_output_conflict')
        return
    save_output(connection, page, 'validated', output, '', now)


def commit_page(connection, page, judgments, now, append_link, *, initial_judgments=None):
    from .continuation_review import VERSION as review_version
    if connection.execute("SELECT 1 FROM continuation_page_commits WHERE page_id=?", (page.page_id,)).fetchone():
        return
    check_page(connection, page)
    output = connection.execute(
        f"SELECT * FROM {output_relation(connection)} WHERE page_id=? AND status='validated'", (page.page_id,),
    ).fetchone()
    original = judgments if initial_judgments is None else initial_judgments
    if output is None or json.loads(output["output_json"]) != judgments_payload(original):
        raise ValueError("continuation_output_mismatch")
    from .day_settlement import parse_continuation_judgments
    if tuple(original) != parse_continuation_judgments(page.plan, json.loads(output["output_json"])):
        raise ValueError("continuation_parsed_identity_mismatch")
    if tuple(judgments) != parse_continuation_judgments(page.plan, judgments_payload(judgments)):
        raise ValueError('continuation_review_identity_mismatch')
    if initial_judgments is not None:
        before = {j.candidate_ref: j for j in original}
        if (set(before) != {j.candidate_ref for j in judgments} or any(
                before[j.candidate_ref].outcome != 'continue' and before[j.candidate_ref] != j for j in judgments)):
            raise ValueError('continuation_review_scope_mismatch')
    by_ref = {item.candidate.candidate_ref: item.candidate for item in page.receipts}
    ordered = sorted(judgments, key=lambda item: (
        next(event.reported_at for event in page.plan.day_events if event.event_id == item.to_event_id), item.candidate_ref))
    for item in ordered:
        candidate = by_ref[item.candidate_ref]
        if (candidate.from_event_id, candidate.match_event_id, candidate.to_event_id, candidate.prior_thread_id) != (
                item.from_event_id, item.match_event_id, item.to_event_id, item.prior_thread_id):
            raise ValueError("continuation_judgment_identity_changed")
        if connection.execute("SELECT 1 FROM day_thread_judgments WHERE settlement_id=? AND candidate_ref=?",
                              (page.settlement_id, item.candidate_ref)).fetchone():
            raise ValueError("continuation_candidate_already_judged")
        link_id = None
        if item.outcome == "continue" and item.confidence >= MIN_CONTINUATION_CONFIDENCE:
            _, link_id = append_link(connection, EventLinkDraft(
                item.from_event_id, item.to_event_id, "continues", item.confidence,
                {"continuation_page_id": page.page_id, "candidate_ref": item.candidate_ref,
                 "page_input_digest": page.input_digest, "prompt_version": page.prompt_version,
                 "match_event_id": item.match_event_id, "reason_codes": list(item.reason_codes),
                 **({'review_version': review_version} if initial_judgments is not None else {})},
            ), now, allow_same_batch=True, allow_reviewed_join=initial_judgments is not None)
        connection.execute("INSERT INTO continuation_page_judgments VALUES (?,?,?,?,?,?,?,?)",
            (page.page_id, page.settlement_id, item.candidate_ref, item.outcome, item.confidence,
             json.dumps(list(item.reason_codes)), link_id, now))
    connection.execute("INSERT INTO continuation_page_commits VALUES (?,?)", (page.page_id, now))


def block_page(connection, page, error_code, now):
    connection.execute("INSERT OR IGNORE INTO continuation_page_blocks VALUES (?,?,?)",
                       (page.page_id, error_code, now))


def recovery_dates(connection, source_namespace, before_active_date):
    """Only already-generated outputs interrupted before their atomic commit."""
    if not tables_available(connection):
        return ()
    from .continuation_raw_checkpoint import returned_dates
    return tuple(sorted(set(returned_dates(connection, source_namespace, before_active_date)) | {row[0] for row in connection.execute(
        "SELECT DISTINCT s.active_date FROM day_settlements s "
        "JOIN continuation_page_requests r ON r.settlement_id=s.id "
        f"JOIN {output_relation(connection)} o ON o.page_id=r.id "
        "LEFT JOIN continuation_page_commits c ON c.page_id=r.id "
        "LEFT JOIN continuation_page_blocks b ON b.page_id=r.id "
        "WHERE s.source_namespace=? AND s.active_date<? AND o.status='validated' "
        "AND c.page_id IS NULL AND b.page_id IS NULL ORDER BY s.active_date",
        (source_namespace, before_active_date))}))


def pending_dates(connection, source_namespace, before_active_date, explicit_retry=False):
    if not tables_available(connection):
        return ()
    rows = connection.execute(
        "SELECT DISTINCT s.active_date,s.id FROM day_settlements s JOIN day_continuation_candidates c ON c.settlement_id=s.id "
        "LEFT JOIN day_thread_judgments d ON d.settlement_id=c.settlement_id AND d.candidate_ref=c.candidate_ref "
        "LEFT JOIN continuation_page_judgments p ON p.settlement_id=c.settlement_id AND p.candidate_ref=c.candidate_ref "
        "WHERE s.source_namespace=? AND s.active_date<? AND d.id IS NULL AND p.candidate_ref IS NULL ORDER BY s.active_date",
        (source_namespace, before_active_date),
    ).fetchall()
    ready = []
    for row in rows:
        try:
            page, _, _, _ = load_page(connection, row[1], explicit_retry)
        except ValueError:
            # Source-invalid frozen evidence needs replanning, not more paid attempts.
            continue
        if page is not None:
            ready.append(row[0])
    return tuple(ready)
