"""One read-only scene view for future Sentinel/Wander consumers.

Time boxes and source identities are stable. Semantic segments are rebuildable
views reconstructed from source-backed day windows, never replacement
conversation indexes. Legacy event groups remain provisional impressions.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict

from time_utils import parse_ts
from .models import digest_batch_sources
from .scene_planner import SceneReplayPlanner, scene_start_date
from .scene_digest_store import SceneDigestStore, cache_path

VERSION = "scene-material-v2"


def _segments(refs, atoms, items):
    """Partition every source exactly once; incomplete/overlapping cuts stay raw."""
    positions = {ref.message_id: i for i, ref in enumerate(refs)}
    candidates = []
    for item in items:
        ids = tuple(dict.fromkeys(item.get("message_ids") or ()))
        if not ids or not set(ids).issubset(positions):
            continue
        indexes = [positions[mid] for mid in ids]
        candidates.append((min(indexes), max(indexes) + 1, item["summary"], ids))
    candidates.sort()
    if any(a[1] > b[0] for a, b in zip(candidates, candidates[1:])):
        return [(0, len(refs), "", "overlapping_settlement", set())]
    if not candidates:
        return [(0, len(refs), "", "provisional", set())]
    # An atomic event spanning a proposed cut still has one source owner. It
    # cannot be silently split just because the day compact is sparse.
    boundaries = {0, len(refs)}
    for start, end, _, _ids in candidates:
        boundaries.update((start, end))
    for atom in atoms:
        indexes = [positions[mid] for mid in atom["message_ids"] if mid in positions]
        if indexes:
            low, high = min(indexes), max(indexes)
            boundaries.difference_update(cut for cut in tuple(boundaries) if low < cut <= high)
    ordered = sorted(boundaries)
    result = []
    for start, end in zip(ordered, ordered[1:]):
        selected = [item for item in candidates if start <= item[0] and item[1] <= end]
        summaries = [item[2] for item in selected]
        covered = {mid for item in selected for mid in item[3]}
        result.append((start, end, "；".join(dict.fromkeys(summaries)), "event_grouped", covered))
    return result


def read_scene_material(source, active_date, *, completed_before="", session_id=None,
                        cache=None, memory_paths=None, settled_projection=None):
    """No model calls/writes. Open or invalidated boxes always return full raw.

All IDs and digests are backend metadata. Use render_scene_material for model
ingredients; this structured view supports local inspection and exact sources.
"""
    from scene_manager.scene import configured_gap_minutes
    from .day_projection import load_day_compact_projection, render_k_facing_summary
    planner = SceneReplayPlanner(source, gap_minutes=configured_gap_minutes())
    boxes = planner.boxes_for_day(active_date, completed_before=completed_before)
    enabled = scene_start_date()
    use_cache = bool(enabled and active_date >= enabled)
    cache = cache or SceneDigestStore(cache_path(source))
    projection = settled_projection
    if projection is None:
        projection = load_day_compact_projection(active_date, memory_paths=memory_paths,
                                                authority_source=source)
    from .scene_corrections import corrected_source_ids, reconcile_boxes
    corrections = corrected_source_ids(active_date, memory_paths, authority_path=source.db_path)
    day_refs = tuple(ref for box in boxes for batch in box.batches for ref in batch.batch_sources)
    # A persisted divider can close the whole day before the calendar cutoff.
    # Reading still verifies closure, exact sources and divider geometry; it
    # never advances the separate generation cutoff or writes a new receipt.
    if use_cache and boxes and all(box.closed for box in boxes):
        from .scene_reconstruction_view import reconstructed_boxes
        reconstructed = reconstructed_boxes(source, active_date, boxes, cache,
                                            completed_before=completed_before, session_id=session_id)
        if reconstructed is not None:
            reconcile_boxes(reconstructed, corrections)
            return {'version': VERSION, 'active_date': active_date, 'boxes': reconstructed,
                    'source_count': sum(box['source_count'] for box in reconstructed),
                    'coverage': 'exact_source_partition', 'reconstruction': 'settled',
                    'time_box_count': len(boxes)}
    groups = cache.load_scene_groups(active_date, projection.get("settlement_id", ""), day_refs, source) if projection else None
    # A sparse day_compact is not evidence of a complete semantic partition.
    items = groups or (projection.get("scene_groups", ()) if projection else ())
    result = []
    manifest = []
    for box in boxes:
        if session_id is not None and box.session_id != session_id:
            continue
        refs = tuple(ref for batch in box.batches for ref in batch.batch_sources)
        rows = source.read_message_ids(tuple(ref.message_id for ref in refs))
        by_id = {row.message_id: row for row in rows}
        encodings = [cache.load(batch, source) for batch in box.batches] if use_cache and box.closed else []
        current = bool(encodings) and all(encoding is not None for encoding in encodings)
        atoms = []
        communications = []
        if current:
            for encoding in encodings:
                communications.extend(encoding.communication_summaries)
                for event in encoding.events:
                    atoms.append({"summary": render_k_facing_summary(event.summary),
                                  "message_ids": [ref.message_id for ref in event.sources],
                                  "vividness": event.importance + abs(event.emotional_weight) * 1.35})
        regions = _segments(refs, atoms, items) if current else [(0, len(refs), "", "raw", set())]
        segments = []
        for start, end, compact_summary, stage, covered in regions:
            segment_refs = refs[start:end]
            ids = {ref.message_id for ref in segment_refs}
            # The compact is navigation, not a full archive. Preserve additional
            # atomic impressions omitted by the compact inside their own window.
            summaries = [render_k_facing_summary(compact_summary)] if compact_summary else []
            retained_atoms = [atom for atom in atoms
                              if atom["message_ids"] and set(atom["message_ids"]).issubset(ids)
                              and not set(atom["message_ids"]).issubset(covered)]
            summaries.extend(atom["summary"] for atom in retained_atoms)
            summary_ids = (set(covered) if compact_summary else set())
            summary_ids.update(mid for atom in retained_atoms for mid in atom["message_ids"])
            summary = "；".join(dict.fromkeys(summary for summary in summaries if summary))
            communication_summaries = [item for item in communications if item['message_id'] in ids]
            raw_reason = ("open_box" if not box.closed else "digest_pending_or_stale" if not current
                          else "no_event_summary" if not (summary or communication_summaries) else "")
            if stage == "overlapping_settlement":
                # Invalid day segmentation cannot manufacture a semantic cut.
                # The valid whole-box encoding still remains usable.
                stage = "provisional"
            digest = digest_batch_sources(segment_refs)
            identity = f"{VERSION}\0{box.box_id}\0{digest}\0{summary}"
            segments.append({"segment_id": "segment_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "parent_box_id": box.box_id, "stage": stage,
                "kind": "summary" if (summary or communication_summaries) and current else "raw", "raw_reason": raw_reason,
                "summary": summary if current else "", "source_digest": digest,
                "communication_summaries": communication_summaries,
                # Summary evidence is narrower than exact box source coverage.
                # Consumers can retain AI-only rows without treating them as
                # accepted mainline memory events or changing the encoder.
                "summary_source_message_ids": [ref.message_id for ref in segment_refs
                                               if current and ref.message_id in summary_ids],
                "vividness": max((atom['vividness'] for atom in atoms
                                  if set(atom['message_ids']).intersection(ids)), default=0.0),
                "source_count": len(segment_refs), "start_ts": segment_refs[0].source_ts,
                "end_ts": segment_refs[-1].source_ts,
                "source_refs": [asdict(ref) for ref in segment_refs],
                "messages": [{"message_id": by_id[ref.message_id].message_id,
                              "timestamp": by_id[ref.message_id].timestamp,
                              "role": by_id[ref.message_id].role,
                              "source_kind": by_id[ref.message_id].source_kind,
                              "content": by_id[ref.message_id].content}
                             for ref in segment_refs if ref.message_id in by_id]})
        emitted = [ref["message_id"] for segment in segments for ref in segment["source_refs"]]
        if emitted != [ref.message_id for ref in refs] or len(set(emitted)) != len(emitted):
            raise ValueError("scene_material_source_partition_failed")
        manifest.extend(refs)
        result.append({"box_id": box.box_id, "session_id": box.session_id,
                       "state": "closed" if box.closed else "open", "digest_ready": current,
                       "source_count": len(refs), "source_digest": digest_batch_sources(refs),
                       "segments": segments})
    # A mutation during read must not publish an impression built from old refs.
    if manifest and not source.validate_batch_sources(tuple(manifest)):
        raise ValueError("scene_material_source_changed")
    result.sort(key=lambda box: parse_ts(box["segments"][0]["start_ts"]))
    reconcile_boxes(result, corrections)
    return {"version": VERSION, "active_date": active_date, "boxes": result,
            "source_count": len(manifest), "coverage": "exact_source_partition",
            "reconstruction": "provisional", "time_box_count": len(boxes)}


def cross_day_projection(material):
    """Adapt complete closed-box impressions to the existing fading recipe.

    Partial or empty encodings do not replace the existing authoritative raw
    fallback. Raw expansion and its budget remain owned by that recipe.
    """
    if not material['boxes'] or any(box['state'] != 'closed' for box in material['boxes']):
        return None
    segments = [segment for box in material['boxes'] for segment in box['segments']]
    if any(segment['kind'] != 'summary' for segment in segments):
        return None
    return {'active_date': material['active_date'], 'items': [
        {'order': order, 'summary': '\n'.join(filter(None, [segment['summary'],
             *communication_texts(segment)])), 'stage': segment['stage'],
         'message_ids': [ref['message_id'] for ref in segment['source_refs']],
         'start_ts': segment['start_ts'], 'end_ts': segment['end_ts'],
         'source_count': segment['source_count'], 'vividness': segment['vividness'],
         'user_source_count': sum(ref.get('source_role') == 'user' for ref in segment['source_refs'])}
        for order, segment in enumerate(segments)]}


def communication_texts(segment):
    messages = {message['message_id']: message for message in segment.get('messages', ())}
    result = []
    for item in segment.get('communication_summaries', ()):
        message = messages.get(item['message_id'])
        if message is not None:
            author = {'user': '人类伙伴', 'assistant': 'AI'}[message['role']]
            clock = parse_ts(message['timestamp']).strftime('%Y-%m-%d %H:%M')
            result.append(f"[{clock}｜{author}当时的通信摘要] {item['summary']}")
    return result


def render_scene_material(material, *, include_communications=True):
    """Objective ingredient only: no policies, instructions or technical IDs."""
    parts = []
    for box in material["boxes"]:
        for segment in box["segments"]:
            if segment["kind"] == "summary":
                start = parse_ts(segment["start_ts"]).strftime("%Y-%m-%d %H:%M")
                end = parse_ts(segment["end_ts"]).strftime("%H:%M")
                if segment['summary']:
                    parts.append(f"[{start}—{end} 的记忆]\n{segment['summary']}")
                if include_communications:
                    parts.extend(communication_texts(segment))
            else:
                for message in segment["messages"]:
                    clock = parse_ts(message["timestamp"]).strftime("%Y-%m-%d %H:%M")
                    role = {"user": "人类伙伴", "assistant": "你", "event": "事件记录",
                            "notification": "系统通知", "system": "系统"}.get(message["role"], message["role"])
                    parts.append(f"[{clock}｜{role}] {message['content']}")
    return "\n\n".join(parts)
