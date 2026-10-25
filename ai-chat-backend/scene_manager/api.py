"""FastAPI endpoints for scene browsing and explicit recollection markers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response

from .anchor import build_anchor_meta
from .scene import (
    configured_gap_minutes,
    get_scene_by_id,
    get_scene_for_ts,
    get_scene_message_ids,
    get_scene_messages,
    get_scenes_for_day,
    add_recollection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scenes", tags=["scenes"])


def _chronicle():
    from event_chronicle import get_global_chronicle

    return get_global_chronicle()


def _default_date() -> str:
    try:
        from time_utils import now_date

        return now_date()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


async def _memory_buckets() -> list[dict]:
    from memory_v2.scene_anchors import load_scene_anchors
    return await load_scene_anchors()


async def _decorate(
    scene: Dict[str, Any],
    include_messages: bool = False,
    memory_buckets: Optional[list[dict]] = None,
) -> Dict[str, Any]:
    chronicle = _chronicle()
    messages = get_scene_messages(scene, chronicle=chronicle)
    if memory_buckets is None:
        memory_buckets = await _memory_buckets()
    anchor_meta = build_anchor_meta(
        scene,
        chronicle=chronicle,
        memory_buckets=memory_buckets,
        message_ids=[str(m.get("message_id") or m.get("id") or "") for m in messages],
    )
    result = dict(scene)
    # Both names are kept for early clients: ``anchor_types`` is the concise
    # list promised by the API, while ``anchors`` leaves room for metadata in
    # later phases.  Neither value is persisted in the scene table.
    result["anchor_types"] = anchor_meta["types"]
    result["anchors"] = anchor_meta
    result["anchor_meta"] = anchor_meta
    result["is_anchor"] = anchor_meta["is_anchor"]
    # The list view needs a lightweight preview without fetching the full
    # message payload a second time.  Keep the source message in the detail
    # response unchanged; this summary is capped for timeline rows.
    result["first_user"] = next(
        (
            str(message.get("content") or "").strip()[:80]
            for message in messages
            if str(message.get("role") or "") == "user"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    if include_messages:
        result["messages"] = messages
    return result


@router.get("")
@router.get("/")
async def list_scenes(
    date: Optional[str] = Query(None, description="活跃日 YYYY-MM-DD"),
    gap_minutes: Optional[int] = Query(None, ge=1, le=1440),
):
    active_date = str(date or _default_date())[:10]
    effective_gap = configured_gap_minutes(gap_minutes)
    # The persisted index follows settings.  A query override is a read-only
    # preview, so get_scenes_for_day re-segments in memory without changing the
    # authoritative scene table.
    chronicle = _chronicle()
    scenes = get_scenes_for_day(
        active_date,
        chronicle=chronicle,
        gap_minutes=effective_gap if gap_minutes is not None else None,
    )
    memory_buckets = await _memory_buckets()
    return {
        "date": active_date,
        "gap_minutes": effective_gap,
        "scenes": [await _decorate(scene, memory_buckets=memory_buckets) for scene in scenes],
    }


@router.get("/trace")
async def trace_bucket(request: Request, response: Response,
                       bucket_id: Optional[str] = Query(None, min_length=1),
                       memory_id: Optional[str] = Query(None, min_length=1)):
    """Resolve a memory bucket's time anchor to its source scene.

    V2 uses exact versioned sources, not an invented single scene for a chain.
    Legacy scene links remain locators, not proof of summary coverage.
    """
    from context_inspection import require_private_inspection, redact
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    if bool(bucket_id) == bool(memory_id):
        raise HTTPException(422, "memory_id 与 bucket_id 二选一")
    reference = str(memory_id or bucket_id)
    if reference.startswith(("event:", "thread:", "group:")):
        import asyncio
        from memory_v2.library import memory_detail, SourceChanged
        try:
            detail = await asyncio.to_thread(memory_detail, reference)
            sources = await asyncio.to_thread(memory_detail, reference, sources=True, limit=5)
            return redact({"memory_id": detail["id"], "pending": False, "scene": None,
                           "memory": detail, "sources": sources})
        except KeyError:
            raise HTTPException(404, "记忆已变化，请刷新") from None
        except SourceChanged:
            raise HTTPException(409, "原文来源已变化，请刷新") from None
        except Exception:
            raise HTTPException(503, "Memory V2 读取暂未就绪") from None
    bucket_id = reference.removeprefix("legacy:")
    try:
        from memory_v2.legacy_archive import load_legacy_bucket

        bucket = await load_legacy_bucket(bucket_id)
    except Exception:
        raise HTTPException(503, "旧资料暂不可读取") from None
    if not bucket:
        raise HTTPException(status_code=404, detail="记忆桶不存在")

    meta = bucket.get("metadata") or {}
    # BucketManager normally nests frontmatter under ``metadata``; accepting
    # top-level fields too keeps the trace contract compatible with lightweight
    # adapters and old callers that already flatten metadata.
    scene_id = str(meta.get("source_scene_id") or bucket.get("source_scene_id") or "")
    source_ts = str(meta.get("source_ts") or bucket.get("source_ts") or "")
    scene = get_scene_by_id(scene_id, chronicle=_chronicle()) if scene_id else None
    if scene is None and source_ts:
        scene = get_scene_for_ts(source_ts, chronicle=_chronicle())
    if scene is None:
        return {
            "bucket_id": bucket_id,
            "source_ts": source_ts,
            "source_scene_id": scene_id,
            "pending": True,
            "source_state": "untraceable",
            "scene": None,
        }
    return redact({
        "bucket_id": bucket_id,
        "source_ts": source_ts,
        "source_scene_id": scene.get("id", ""),
        "pending": False,
        "source_state": "legacy_scene_locator",
        "scene": await _decorate(scene, include_messages=True),
    })


@router.get("/{scene_id}")
async def scene_detail(scene_id: str):
    scene = get_scene_by_id(scene_id, chronicle=_chronicle())
    if scene is None:
        raise HTTPException(status_code=404, detail="scene 不存在")
    return await _decorate(scene, include_messages=True)


@router.post("/{scene_id}/recollection")
async def recollection_marker(scene_id: str, payload: Optional[Dict[str, Any]] = Body(None)):
    """Add/remove a user-selected recollection marker.

    Accepted bodies are ``{"action": "add|remove"}`` or
    ``{"delta": 1|-1}``; an empty body adds one marker.
    """
    chronicle = _chronicle()
    scene = get_scene_by_id(scene_id, chronicle=chronicle)
    if scene is None:
        raise HTTPException(status_code=404, detail="scene 不存在")
    payload = payload or {}
    if "delta" in payload:
        try:
            delta = int(payload.get("delta"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="delta 必须是整数")
        delta = 1 if delta > 0 else -1 if delta < 0 else 0
    else:
        action = str(payload.get("action", "add")).lower()
        if action in ("remove", "delete", "unset", "off"):
            delta = -1
        elif action in ("add", "set", "on", "mark"):
            delta = 1
        else:
            raise HTTPException(status_code=400, detail="action 必须是 add 或 remove")

    updated = add_recollection(scene_id, delta=delta, chronicle=chronicle)
    decorated = await _decorate(updated or scene)
    return {
        "scene_id": scene_id,
        "recollection_count": int((updated or scene).get("recollection_count") or 0),
        "anchor_types": decorated["anchor_types"],
        "scene": decorated,
    }
