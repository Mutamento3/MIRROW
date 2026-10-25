"""Private read-only memory library over the existing recall graph.

No encoder, search planner, migration or new memory authority. Document IDs
identify the current projection; event IDs identify immutable chain nodes.
"""
from dataclasses import asdict, replace

from behavior_scheduler.bookshelf_search import sanitize_archive_text
from .context_shadow import recall_service_snapshot
from .legacy_archive import get_legacy_archive
from .recall import RecallHit
from .recall_evidence import RecallEvidencePolicy, expand_recall_evidence
from .source_validation import (
    SourceChanged,
    nodes_and_proofs as _nodes_and_proofs,
    validate_sources as _validate_sources,
)


def _documents(service, excluded):
    blocked = set(excluded)
    return tuple(doc for doc in service.index.load_documents()
                 if doc.source_layer in {"event", "group"}
                 and not blocked.intersection(doc.source_message_ids))


def _find(documents, memory_id):
    # Stable event IDs still resolve after an event joins a thread.
    return next((doc for doc in documents if doc.document_id == memory_id
                 or doc.document_id == "thread:" + memory_id
                 or memory_id.removeprefix("event:") in doc.event_ids), None)


def _card(doc):
    text = "\n".join(doc.summaries)
    return {"id": doc.document_id, "kind": "thread" if doc.document_id.startswith("thread:") else doc.source_layer,
            "summary": sanitize_archive_text(text, limit=600), "summary_truncated": len(text) > 600,
            "date_from": doc.date_from, "date_to": doc.date_to,
            "event_count": len(doc.event_ids), "source_count": len(doc.source_message_ids),
            "participants": list(doc.participant_ids), "facets": list(doc.facets),
            "confidence": doc.confidence, "read_only": True, "source": "Memory V2",
            "summary_origin": "source_review" if doc.reviewed_event_ids else "encoded"}


def list_memories(*, offset=0, limit=40, query=""):
    with recall_service_snapshot() as (service, excluded):
        documents = _documents(service, excluded)
        term = str(query).strip().casefold()
        selected = [doc for doc in documents if not term or term in doc.search_text.casefold()]
        selected.sort(key=lambda doc: (doc.date_to, doc.document_id), reverse=True)
        return {"memories": [_card(doc) for doc in selected[offset:offset + limit]],
                "total": len(selected), "offset": offset, "limit": limit,
                "source": "Memory V2", "read_only": True}


def library_status():
    with recall_service_snapshot() as (service, excluded):
        documents = _documents(service, excluded)
        return {"status": "ready", "source": "Memory V2", "legacy_runtime": "retired",
                "total": len(documents), "event_count": len({event for doc in documents for event in doc.event_ids}),
                "thread_count": sum(doc.document_id.startswith("thread:") for doc in documents),
                "group_count": sum(doc.source_layer == "group" for doc in documents),
                "quarantined_source_count": len(excluded), "read_only": True}


def memory_detail(memory_id, *, offset=0, limit=20, sources=False):
    if offset < 0 or limit < 1 or limit > (5 if sources else 100):
        raise ValueError("invalid_page")
    with recall_service_snapshot() as (service, excluded):
        doc = _find(_documents(service, excluded), memory_id)
        if doc is None:
            raise KeyError("memory_not_found")
        nodes, proofs = _nodes_and_proofs(service, doc)
        _validate_sources(service, doc, proofs)
        summaries = dict(zip(doc.event_ids, doc.summaries))
        nodes = [{**node, 'summary': summaries[node['id']],
                  'stored_summary': node['summary'],
                  'summary_origin': 'source_review' if node['id'] in doc.reviewed_event_ids else 'encoded'}
                 for node in nodes]
        if not sources:
            # Group records have source-backed navigation text but no V2 event nodes.
            return {**_card(doc), "nodes": [{**node, "summary": sanitize_archive_text(node["summary"], limit=4000),
                                             "summary_truncated": len(node["summary"]) > 4000}
                                            for node in nodes[offset:offset + limit]],
                    "node_total": len(nodes), "offset": offset, "limit": limit,
                    "source_state": "verified_current", "event_ids": list(doc.event_ids),
                    "summaries": [sanitize_archive_text(text, limit=4000) for text in doc.summaries]
                    if not nodes else []}
        ids = doc.source_message_ids[offset:offset + limit]
        selected = replace(doc, source_message_ids=ids,
                           source_anchors=tuple(anchor for anchor in doc.source_anchors if anchor.message_id in ids))
        hit = RecallHit(selected, 0, 0, 0, None, 0, False, ())
        # Exact anchors, not adjacent conversations. Page limits and explicit
        # truncation allow browsing a long chain without a silent fixed cutoff.
        bundle = expand_recall_evidence(service.source, [hit], needs_source_detail=True,
            policy=RecallEvidencePolicy(max_anchors_per_hit=10, before=0, after=0,
                                        max_message_chars=2000, max_block_chars=12000)) if ids else None
        groups = bundle.groups if bundle else ()
        if any(group.missing_anchor_count for group in groups):
            raise SourceChanged("original_changed_during_read")
        # Re-read originals after expansion to detect mutations during the read.
        _validate_sources(service, doc, proofs)
        return {"id": doc.document_id, "messages": [asdict(message) for group in groups for message in group.messages],
                "total": len(doc.source_message_ids), "offset": offset, "limit": limit,
                "truncated": bool(bundle and bundle.truncated),
                "omitted_in_page": len(ids) - sum(len(group.messages) for group in groups),
                "source_state": "verified_current", "read_only": True}


def archive_records(*, offset=0, limit=40, query=""):
    archive = get_legacy_archive()
    items = archive.list_buckets()
    term = str(query).strip().casefold()
    selected = [item for item in items if not term or term in
                (str(item["metadata"].get("name", "")) + " " + item["content"]).casefold()]
    return {"memories": [{"id": "legacy:" + item["id"], "kind": "legacy",
            "name": str(item["metadata"].get("name") or "旧资料"),
            "summary": sanitize_archive_text(item["content"], limit=600),
            "summary_truncated": len(item["content"]) > 600, "source": "旧资料（未按 V2 重新核验）",
            "read_only": True, "archive_folder": item["archive_folder"]}
            for item in selected[offset:offset + limit]], "total": len(selected), "offset": offset,
            "limit": limit, "complete": not archive.read_errors, "read_error_counts": archive.read_errors,
            "read_only": True}


def archive_detail(memory_id):
    item = get_legacy_archive().get(memory_id.removeprefix("legacy:"))
    if item is None:
        raise KeyError("archive_not_found")
    keys = ("source_scene_id", "source_ts", "source_date", "latest_source_date", "created", "tags", "type")
    return {"id": "legacy:" + item["id"], "name": str(item["metadata"].get("name") or "旧资料"),
            "content": sanitize_archive_text(item["content"], limit=100000),
            "content_truncated": len(item["content"]) > 100000, "read_only": True,
            "source_state": "legacy_link_unverified", "metadata": {
                key: item["metadata"][key] for key in keys if key in item["metadata"]}}


def coverage_observation():
    """Body-free inventory; scene/date overlaps never prove semantic coverage.

    Old summaries have no immutable per-message revision manifest. Until such
    provenance can be proved, all overlap is unproven and nothing is removed
    from fallback recall. This audit deliberately makes no semantic guesses.
    """
    archive = get_legacy_archive()
    items = archive.list_buckets()
    with recall_service_snapshot() as (service, excluded):
        documents = _documents(service, excluded)
        dates = {value for doc in documents for value in (doc.date_from, doc.date_to) if value}
        counts = {"scene_link_unproven": 0, "time_overlap_unproven": 0, "unverifiable": 0}
        for item in items:
            meta = item["metadata"]
            if meta.get("source_scene_id"):
                counts["scene_link_unproven"] += 1
            elif any(str(meta.get(key) or "")[:10] in dates for key in ("source_date", "latest_source_date", "source_ts")):
                counts["time_overlap_unproven"] += 1
            else:
                counts["unverifiable"] += 1
        return {"archive_total": len(items), "current_document_total": len(documents),
                "exact_coverage_proven": 0, "classification_counts": counts,
                "complete": not archive.read_errors, "read_error_counts": archive.read_errors,
                "automatic_removal": False, "model_calls": 0,
                "note": "旧资料缺少不可变逐消息版本证明；场景和日期线索不等于内容已覆盖"}
