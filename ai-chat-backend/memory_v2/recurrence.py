"""Recurring-scene read projection, independent from occurrence threads.

A family label is a model-produced navigation tag, not a claim that two
events are the same occurrence or that the archive covers all life events.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path


def validated_label(label, confidence):
    if (not isinstance(label, str) or not 2 <= len(label.strip()) <= 32 or "\n" in label
            or type(confidence) not in (int, float) or not math.isfinite(confidence)
            or not 0.85 <= confidence <= 1):
        return ""
    return label.strip()


def labels_from_attributes(attributes):
    labels = {validated_label(item.get("recurrence_label"), item.get("recurrence_confidence"))
              for item in attributes}
    labels.discard("")
    # A changing long thread is not one repetitive family if its nodes disagree.
    return next(iter(labels)) if len(labels) == 1 and all(
        validated_label(item.get("recurrence_label"), item.get("recurrence_confidence"))
        for item in attributes) else ""


def catalog(paths, *, limit=24):
    labels = []
    for raw in paths:
        path = Path(raw).resolve()
        if not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                rows = db.execute("SELECT attributes_json FROM events e WHERE COALESCE("
                    "(SELECT status FROM event_status_log s WHERE s.event_id=e.id "
                    "ORDER BY created_at DESC,id DESC LIMIT 1),'active')='active' "
                    "ORDER BY reported_at DESC LIMIT 200").fetchall()
            for row in rows:
                item = json.loads(row[0])
                label = validated_label(item.get("recurrence_label"), item.get("recurrence_confidence"))
                if label and label not in labels:
                    labels.append(label)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            continue
    return tuple(labels[:limit])


def family_key(document):
    """Return the source-backed recurrence family identity, if eligible."""

    if (document.source_layer != "event" or not document.recurrence_label
            or document.recurrence_members):
        return None
    return (document.recurrence_label, tuple(sorted(document.subject_ids)),
            tuple(sorted(document.participant_ids)), tuple(sorted(document.source_scope_ids)))


def diversify_ranked_documents(documents, limit):
    """Reserve shortlist room before truncation, without rewriting relevance.

    Only already trusted families share a soft quota. Keep several members for
    development/exact-instance reading; unused room goes back to original rank.
    Output keeps original relative order, not a synthetic relevance score.
    """
    if limit <= 0:
        raise ValueError('candidate shortlist limit must be positive')
    if len(documents) <= limit:
        return tuple(documents)
    quota = max(2, limit // 4)
    counts, chosen = {}, set()
    for position, document in enumerate(documents):
        key = family_key(document)
        if key is not None and counts.get(key, 0) >= quota:
            continue
        chosen.add(position)
        if key is not None:
            counts[key] = counts.get(key, 0) + 1
        if len(chosen) == limit:
            break
    if len(chosen) < limit:
        for position in range(len(documents)):
            chosen.add(position)
            if len(chosen) == limit:
                break
    return tuple(documents[position] for position in sorted(chosen))


def compact_candidates(candidates):
    """Give a repeated family one slot while retaining distinguishing episodes.

Grouping is only within this retrieved shortlist, never a lifetime-frequency
estimate. Unlabelled or low-confidence events remain independent candidates.
"""
    groups = {}
    for rank, hit in candidates:
        doc = hit.document
        key = family_key(doc)
        if key is None:
            continue
        groups.setdefault(key, []).append((rank, hit))
    projected, suppressed = {}, set()
    for key, members in groups.items():
        unique = {hit.document.document_id for _, hit in members}
        if len(unique) < 3:
            continue
        leader_rank, leader = members[0]
        chronological = sorted(members, key=lambda pair: (pair[1].document.date_from,
                                                         pair[1].document.active_date_from, pair[0]))
        # Match, archive-first and archive-latest are all source-backed records.
        reps = {leader_rank, chronological[0][0], chronological[-1][0]}
        # A specific wording match may rank below a broader semantic leader.
        # Retain one such member and let the existing detail path open its own
        # proof, rather than an archive-first representative's neighborhood.
        preferred = next((pair for pair in members if pair[1].direct_match), members[0])
        reps.add(preferred[0])
        # A salient change can survive even when it is not the closest match.
        salient = max(members, key=lambda pair: pair[1].document.importance)
        reps.add(salient[0])
        chosen = [pair for pair in chronological if pair[0] in reps]
        proof_order = [preferred, *(pair for pair in chosen if pair[0] != preferred[0])]
        def impression(hit):
            doc = hit.document
            if doc.display_text:
                # A singleton's source-backed impression belongs to that
                # event. A multi-node impression has no per-node attribution.
                return doc.display_text, doc.event_nodes if len(doc.event_ids) == 1 else ()
            summaries = doc.summaries
            indices = tuple(range(len(summaries)))
            if len(summaries) > 3:
                match = max(range(len(summaries)), key=lambda i: sum(
                    term.casefold() in summaries[i].casefold() for term in hit.matched_terms))
                indices = tuple(dict.fromkeys((0, match, len(summaries) - 1)))
            nodes = (tuple(doc.event_nodes[i] for i in indices)
                     if len(doc.event_nodes) == len(summaries) else ())
            return "；".join(summaries[i] for i in indices), nodes
        samples = tuple(((hit.document.date_from or hit.document.active_date_from or '日期未知'),
                         *impression(hit)) for _, hit in chosen)
        text = f"这次想起的「{key[0]}」有 {len(unique)} 份同类经历记录（不同经历，不是同一事件链；不是完整发生次数）。"
        from habit_radar.frequency import FrequencyEvidence, count_frequencies
        music = []
        for _, hit in members:
            for record in hit.document.recorded_music:
                try:
                    music.append(FrequencyEvidence(**{field: record[field] for field in
                        ("subject_id", "pattern_key", "occurrence_key", "discussion_key",
                         "source_message_id", "source_date", "temporal_status")}))
                except (KeyError, TypeError):
                    continue
        if music:
            count = sum(item["recorded_occurrences"] for item in count_frequencies(music))
            text += f" 这些记录中记有 {count} 次歌曲材料经历，已去掉同次重复记录；不等于实际播放次数。"
        text += "\n" + "\n".join(when + "：" + text for when, text, _ in samples)
        projected[leader_rank] = replace(leader, direct_match=preferred[1].direct_match,
            matched_terms=preferred[1].matched_terms,
            document=replace(leader.document, display_text=text,
            # Every displayed representative retains its own event and source
            # proofs. Family navigation must not borrow the leader's anchors.
            event_ids=tuple(dict.fromkeys(e for _, hit in chosen for e in hit.document.event_ids)),
            source_message_ids=tuple(dict.fromkeys(m for _, hit in proof_order for m in hit.document.source_message_ids)),
            source_anchors=tuple(dict.fromkeys(a for _, hit in proof_order for a in hit.document.source_anchors)),
            summaries=tuple(s for _, hit in chosen for s in hit.document.summaries),
            event_nodes=tuple(n for _, hit in chosen for n in hit.document.event_nodes),
            reading_companions=(),
            confidence=min(hit.document.confidence for _, hit in chosen),
            recurrence_members=tuple(hit.document.document_id for _, hit in members),
            reviewed_event_ids=tuple(dict.fromkeys(e for _, hit in chosen for e in hit.document.reviewed_event_ids)),
            reviewed_source_versions=tuple(dict.fromkeys(p for _, hit in chosen
                                                        for p in hit.document.reviewed_source_versions)),
            recurrence_samples=tuple((when, text) for when, text, _ in samples),
            recurrence_sample_nodes=tuple(nodes for _, _, nodes in samples),
            date_from=min(hit.document.date_from for _, hit in members),
            date_to=max(hit.document.date_to for _, hit in members),
            active_date_from=min(hit.document.active_date_from for _, hit in members),
            active_date_to=max(hit.document.active_date_to for _, hit in members)))
        suppressed.update(rank for rank, _ in members[1:])
    return tuple((rank, projected.get(rank, hit)) for rank, hit in candidates if rank not in suppressed)
