"""Shared read-only proof checks for memory details and linked projections."""
from contextlib import closing
from pathlib import Path
import sqlite3

from .conversation_source import digest_text
from .recall import EventRecallIndex


class SourceChanged(ValueError):
    """The current original no longer proves this indexed memory."""


def nodes_and_proofs(service, doc):
    nodes, proofs = {}, []
    for index in service.index.indexes:
        # Conversation/group/cognition adapters subclass EventRecallIndex but
        # point at the raw authority, which deliberately has no events table.
        if type(index) is not EventRecallIndex:
            continue
        # mode=ro also prevents an absent configured store from being created.
        with closing(sqlite3.connect(Path(index.db_path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            for start in range(0, len(doc.event_ids), 400):
                ids = doc.event_ids[start:start + 400]
                if not ids:
                    continue
                marks = ",".join("?" for _ in ids)
                for row in connection.execute(f"SELECT id, summary, occurred_at, reported_at, active_date, "
                    f"confidence, epistemic_status FROM events e WHERE id IN ({marks}) AND COALESCE("
                    "(SELECT status FROM event_status_log s WHERE s.event_id=e.id "
                    "ORDER BY created_at DESC,id DESC LIMIT 1),'active')='active'", ids):
                    nodes[row["id"]] = dict(row)
                proofs.extend(dict(row) for row in connection.execute(
                    f"SELECT s.*, b.content_digest, b.active_date, b.calendar_date FROM event_sources s JOIN events e ON e.id=s.event_id "
                    "JOIN encoding_batch_sources b ON b.batch_id=e.batch_id AND b.message_id=s.message_id "
                    f"WHERE s.event_id IN ({marks})", ids))
    if set(nodes) != set(doc.event_ids):
        raise SourceChanged("event_projection_changed")
    return [nodes[event_id] for event_id in doc.event_ids], proofs


def validate_sources(service, doc, proofs):
    if doc.reviewed_source_versions:
        from .source_reviews import reviewed_messages
        try:
            reviewed_messages(service.source, doc)
        except (ValueError, OSError, sqlite3.Error):
            raise SourceChanged('review_source_changed') from None
    messages = {message.message_id: message for message in service.source.read_message_ids(doc.source_message_ids)}
    if set(messages) != set(doc.source_message_ids):
        raise SourceChanged("original_unavailable")
    if doc.event_ids and {proof["message_id"] for proof in proofs} != set(doc.source_message_ids):
        raise SourceChanged("source_proof_unavailable")
    for proof in proofs:
        message = messages[proof["message_id"]]
        if (message.row_id != proof["message_row_id"] or message.session_id != proof["session_id"]
                or message.timestamp != proof["source_ts"] or message.role != proof["source_role"]
                or message.source_kind != proof["source_kind"]
                or message.active_date != proof["active_date"] or message.calendar_date != proof["calendar_date"]
                or message.event_type != proof["source_event_type"]
                or digest_text(message.content) != proof["content_digest"]):
            raise SourceChanged("original_revision_changed")
        if proof["span_start"] is not None or proof["span_end"] is not None:
            start, end = proof["span_start"], proof["span_end"]
            if (start is None or end is None or not 0 <= start < end <= len(message.content)
                    or digest_text(message.content[start:end]) != proof["span_digest"]):
                raise SourceChanged("anchor_revision_changed")
    for anchor in doc.source_anchors:
        if anchor.span_start is None and anchor.span_end is None:
            continue
        message = messages[anchor.message_id]
        if (anchor.span_start is None or anchor.span_end is None
                or not 0 <= anchor.span_start < anchor.span_end <= len(message.content)
                or digest_text(message.content[anchor.span_start:anchor.span_end]) != anchor.span_digest):
            raise SourceChanged("anchor_revision_changed")
