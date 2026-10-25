"""Private scene inspection. GETs never run an encoder or write a cache."""
from datetime import date
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, Response
from context_inspection import require_private_inspection

router = APIRouter(prefix="/api/memory-v2/scenes", tags=["memory-v2-scenes"])


async def load_current(active_date):
    try:
        if date.fromisoformat(active_date).isoformat() != active_date:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from None
    # A read has no authority to close a currently active day. The normal
    # completed-day reader is wired at startup; an unavailable cutoff is blank.
    from event_chronicle import get_global_chronicle
    from . import scene_digest_runtime
    from .conversation_source import ConversationSource
    from .scene_material import read_scene_material
    cutoff = await scene_digest_runtime.completed_before(active_date)
    try:
        return read_scene_material(ConversationSource(get_global_chronicle().db_path,
                                   canonical_sessions_only=True), active_date, completed_before=cutoff)
    except ValueError:
        raise HTTPException(409, "盒子来源正在变化，请刷新") from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "盒子来源暂时不可读取") from None


@router.get("/{active_date}")
async def inspect_day(active_date: str, request: Request, response: Response,
                      offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    material = await load_current(active_date)
    boxes = []
    for box in material["boxes"][offset:offset + limit]:
        segments = [{key: value for key, value in segment.items()
                     if key not in {"messages", "source_refs", "summary_source_message_ids"}}
                    for segment in box["segments"]]
        boxes.append({**box, "segments": segments})
    from context_inspection import redact
    return redact({**material, "boxes": boxes, "total_boxes": len(material["boxes"]), "offset": offset})


@router.get("/{active_date}/segments/{segment_id}/sources")
async def inspect_sources(active_date: str, segment_id: str, request: Request, response: Response,
                          offset: int = Query(0, ge=0), limit: int = Query(40, ge=1, le=80)):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    material = await load_current(active_date)
    segment = next((segment for box in material["boxes"] for segment in box["segments"]
                    if segment["segment_id"] == segment_id), None)
    if segment is None:
        raise HTTPException(404, "盒子来源已变化，请刷新")
    from context_inspection import redact
    return {"segment_id": segment_id, "source_digest": segment["source_digest"],
            "total": len(segment["messages"]), "offset": offset,
            "messages": redact(segment["messages"][offset:offset + limit])}
