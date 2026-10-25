"""Ephemeral process reading, shared by chat, active search and other bodies.

Ranking proposes related records; only persisted threads establish continuity.
There is no model call, graph mutation, completion classifier or new authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Sequence

from .recall import (
    EventRecallIndex, RecallDocument, RecallEventNode, RecallHit, RecallQuery,
    RecallResult, _normalized, _query_terms,
)


@dataclass(frozen=True)
class ReadingPoint:
    document: RecallDocument
    node: RecallEventNode
    label: str
    relation: str = "record"


from .state_changes import search_text as progress_text

def occurrence_key(node: RecallEventNode) -> tuple:
    """Unknown occurrence times are not sorted as later developments."""
    value = node.occurred_at if node.temporal_basis != "unresolved" else ""
    try:
        from .source_time import instant
        stamp = instant(value)
    except ValueError:
        return (1, node.reported_at, node.event_id)
    return (0, stamp.timestamp(), node.event_id)


def _match_node(nodes: Sequence[RecallEventNode], terms: Sequence[str]) -> RecallEventNode:
    return max(nodes, key=lambda node: (
        sum(_normalized(term) in _normalized(progress_text(node.summary, node.state_changes)) for term in terms if term),
        len(node.summary) > 0,
        -nodes.index(node),
    ))


def representative_points(hit: RecallHit) -> tuple[ReadingPoint, ...]:
    """First, match and last-known point retain separate clocks and provenance."""
    document = hit.document
    if document.source_layer != "event" or document.recurrence_members:
        return ()
    nodes = tuple(sorted(document.event_nodes, key=occurrence_key))
    if not nodes:
        return ()
    known = tuple(node for node in nodes if occurrence_key(node)[0] == 0)
    unknown = tuple(node for node in nodes if occurrence_key(node)[0] != 0)
    match = _match_node(nodes, hit.matched_terms)
    picked: dict[str, str] = {}
    if known:
        picked[known[0].event_id] = "起点" if len(known) > 1 else ""
        if match in known:
            picked[match.event_id] = "命中记录"
        # A singleton is a dated record, not a claim about the current state.
        picked[known[-1].event_id] = "最后已知记录" if len(known) > 1 else ""
    from .state_changes import latest_progress_nodes
    progress_ids = latest_progress_nodes(nodes)
    for node in nodes:
        if node.event_id in progress_ids and node.event_id not in picked:
            picked[node.event_id] = '最近明确进展'
    if unknown and (match in unknown or not known):
        unresolved = match if match in unknown else unknown[-1]
        picked[unresolved.event_id] = "发生时间未确定"
    relation = "thread" if document.document_id.startswith("thread:") else "record"
    points = [ReadingPoint(document, node, picked[node.event_id], relation)
              for node in nodes if node.event_id in picked]
    for companion in document.reading_companions:
        # It remains its own document, with its own sources and thread identity.
        child = replace(hit, document=replace(companion, reading_companions=()))
        child_points = representative_points(child)
        known_points = [p for p in child_points if occurrence_key(p.node)[0] == 0]
        if child_points:
            point = known_points[-1] if known_points else child_points[-1]
            points.append(replace(point, label="可能相关记录", relation="candidate"))
    return tuple(points)


class _ReadingIndex(EventRecallIndex):
    def __init__(self, parent: EventRecallIndex, documents: Sequence[RecallDocument]):
        super().__init__(parent.db_path, semantic_scorer=parent.semantic_scorer)
        self.documents = tuple(documents)

    def _load_documents(self):
        return list(self.documents)


def _reading_subjects(document: RecallDocument) -> set[str]:
    # Recall nodes contain only changes whose current authoritative source has
    # passed the read-time validator. A speaker is not always the fact subject.
    return set(document.subject_ids) | {
        change['subject_id'] for node in document.event_nodes
        for change in node.state_changes if change.get('subject_id')
    }


def _eligible(document: RecallDocument, seed: RecallDocument, query: RecallQuery) -> bool:
    # Filter BEFORE the bounded candidate window; another work/subject cannot
    # occupy all seats and then be discarded after ranking.
    return bool(
        document.source_layer == "event" and document.event_nodes
        and not document.recurrence_members and not document.recurrence_label
        and _reading_subjects(document) == _reading_subjects(seed)
        and set(document.source_scope_ids) == set(seed.source_scope_ids)
        and not set(document.event_ids).intersection(seed.event_ids)
        and EventRecallIndex._matches_filters(
            document, replace(query, date_from="", date_to="")
        )
        and not set(document.source_message_ids).intersection(seed.source_message_ids)
    )


def enrich_process_reading(index: EventRecallIndex, query: RecallQuery,
                           result: RecallResult) -> RecallResult:
    """Check at most two seeds, rank at most 24 candidates each, add one each.

    A second search follows the hit's facts rather than only the original
    question. Dates that located the seed do not pretend to bound its later
    developments. Explicit source exclusions and other filters still apply.
    High-frequency families keep their existing independent-occurrence view.
    """
    seeds = [hit for hit in result.hits if hit.document.source_layer == "event"
             and hit.document.event_nodes and not hit.document.recurrence_members
             and not hit.document.recurrence_label and not hit.document.reading_companions][:2]
    if not seeds:
        return result
    documents = index.load_documents()
    replacements: dict[str, RecallHit] = {}
    used: set[str] = set()
    seed_count = candidate_count = evaluated_count = 0
    for hit in seeds:
        seed = hit.document
        if seed.document_id in used:
            continue
        seed_count += 1
        node = _match_node(seed.event_nodes, _query_terms("\n".join((query.text, *query.variants))))
        eligible = [doc for doc in documents if _eligible(doc, seed, query)]
        if not eligible:
            continue
        candidate_count += len(eligible)
        terms = tuple(term for term in _query_terms(progress_text(node.summary, node.state_changes))
                      if term not in {"human", "人类伙伴", "洛临", "临渊", "伙伴", "伙伴"})
        # Rarity nominates candidates, not event identity: a well-established
        # matter can itself be frequent. Persisted family metadata is the
        # independent-occurrence boundary above; similarity never adds an edge.
        population = (*eligible, seed)
        texts = {doc.document_id: _normalized("\n".join(doc.summaries)) for doc in population}
        rare = tuple(term for term in terms if len(term) >= 2 and
                     sum(term in texts[doc.document_id] for doc in population)
                     <= max(3, len(population) // 10))
        eligible = [doc for doc in eligible if
                    sum(term in texts[doc.document_id] for term in terms) >= 2]
        if not eligible:
            continue
        followup = replace(query, text=progress_text(node.summary, node.state_changes), variants=(), entity_terms=(),
                           subject_ids=(), date_from="", date_to="", sort_mode="relevance",
                           context_text='', automatic=False)
        selector = getattr(index, "candidate_selector", None)
        if selector is not None:
            nominated = selector.select(eligible, followup, limit=22)
        else:
            # Local lexical nomination bounds embedding/ranking even for a
            # basic index without the warmed FTS selector.
            nominated = tuple(sorted(eligible, key=lambda doc: (
                -sum(term in texts[doc.document_id] for term in rare),
                -sum(term in texts[doc.document_id] for term in terms),
                doc.document_id,
            ))[:22])
        known_documents = [doc for doc in eligible if
                           any(occurrence_key(n)[0] == 0 for n in doc.event_nodes)]
        latest = sorted(known_documents, key=lambda doc: max(
            occurrence_key(n)[1] for n in doc.event_nodes if occurrence_key(n)[0] == 0
        ), reverse=True)[:2]
        candidates = tuple({doc.document_id: doc for doc in (*latest, *nominated)}.values())[:24]
        ranked = _ReadingIndex(index, candidates).search(followup, limit=24)
        evaluated_count += ranked.evaluated_count or 0
        candidates = [item for item in ranked.hits if item.score >= 0.16
                      and item.document.document_id not in used
                      and item.document.document_id not in replacements]
        # Ranking against the seed is nomination only. A temporary companion
        # must independently concern the current question; persisted thread
        # nodes above are never subjected to this vocabulary-dependent filter.
        from .recall_admission import recall_support_level
        current = _ReadingIndex(index, [item.document for item in candidates]).search(
            replace(query, date_from='', date_to='', sort_mode='relevance'), limit=24)
        supported = {item.document.document_id for item in current.hits
                     if recall_support_level(item) == 'strong'}
        candidates = [item for item in candidates if item.document.document_id in supported]
        if not candidates:
            continue
        # Latest *occurrence* among sufficiently similar records is useful for
        # stale-progress checks. It is still labeled candidate, not latest state.
        sufficiently_related = [item for item in candidates
                                if item.score >= candidates[0].score * 0.75]
        known = [item for item in sufficiently_related if
                 any(occurrence_key(n)[0] == 0 for n in item.document.event_nodes)]
        companion = max(known, key=lambda item: max(
            occurrence_key(n)[1] for n in item.document.event_nodes if occurrence_key(n)[0] == 0
        )) if known else sufficiently_related[0]
        used.add(companion.document.document_id)
        replacements[seed.document_id] = replace(hit, document=replace(
            seed, reading_companions=(companion.document,)))
    def updated(hit):
        return replacements.get(hit.document.document_id, hit)
    return replace(result, hits=tuple(updated(hit) for hit in result.hits),
                   decisions=tuple(replace(d, hit=updated(d.hit)) for d in result.decisions),
                   reading_seed_count=seed_count, reading_candidate_count=candidate_count,
                   reading_evaluated_count=evaluated_count)


def search_reading(index: EventRecallIndex, query: RecallQuery, *, limit=5) -> RecallResult:
    return enrich_process_reading(index, query, index.search(query, limit=limit))
