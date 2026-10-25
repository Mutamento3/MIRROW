"""事件纪 API 路由 — 从 main.py 提取（重要事件 + 日记 + 维护）"""

import uuid
import asyncio
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from time_utils import now_iso
from llm_client import call_llm_flash_for_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


# ═══════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════

class ImportantEventCreateRequest(BaseModel):
    date_string: str
    date_precision: str = "full"
    event_text: str
    emotion: str = ""


class ImportantEventUpdateRequest(BaseModel):
    date_string: Optional[str] = None
    date_precision: Optional[str] = None
    event_text: Optional[str] = None
    emotion: Optional[str] = None


class ImportantEventResponse(BaseModel):
    id: str
    date_string: str
    date_precision: str
    event_text: str
    emotion: str
    created_at: str
    updated_at: str


class EventSuggestionRequest(BaseModel):
    user_text: str
    session_id: str = ""


class EventSuggestionResponse(BaseModel):
    should_suggest: bool
    confidence: float
    suggested_text: str
    suggested_date: str
    suggested_emotion: str
    reason: str


class EventSuggestionSuppressRequest(BaseModel):
    session_id: str


class DiaryEntryResponse(BaseModel):
    id: str
    date: str
    content: str
    summary_ids: List[str]
    generated_at: str


class MemoryBoxSourceResponse(BaseModel):
    timestamp: str
    role: str
    content: str


class MemoryBoxResponse(BaseModel):
    order: int
    start_ts: str
    end_ts: str
    summary: str
    event_count: int
    source_count: int
    sources: List[MemoryBoxSourceResponse]
    box_id: str = ""
    segment_id: str = ""
    active_date: str = ""
    state: str = ""
    stage: str = ""
    kind: str = ""
    raw_reason: str = ""
    preview_truncated: bool = False


class MemoryBoxesResponse(BaseModel):
    date: str
    created_at: str
    boxes: List[MemoryBoxResponse]


class MaintenanceTaskResponse(BaseModel):
    name: str
    status: str
    progress: int
    message: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None


class MaintenanceStatusResponse(BaseModel):
    tasks: List[MaintenanceTaskResponse]
    last_run: Optional[str] = None
    last_success: Optional[str] = None
    overall_progress: int


# ═══════════════════════════════════════════════════════════
# 重要事件 CRUD
# ═══════════════════════════════════════════════════════════

@router.post("/important", response_model=ImportantEventResponse)
async def create_important_event(request: ImportantEventCreateRequest):
    """创建重要事件"""
    from event_chronicle import get_global_chronicle, ImportantEvent
    chronicle = get_global_chronicle()
    now = now_iso()
    event_id = f"imp_{uuid.uuid4().hex[:12]}"
    event = ImportantEvent(
        id=event_id, date_string=request.date_string,
        date_precision=request.date_precision,
        event_text=request.event_text, emotion=request.emotion,
        created_at=now, updated_at=now
    )
    chronicle.add_important_event(event)
    return ImportantEventResponse(**{
        "id": event.id, "date_string": event.date_string,
        "date_precision": event.date_precision,
        "event_text": event.event_text, "emotion": event.emotion,
        "created_at": event.created_at, "updated_at": event.updated_at
    })


@router.get("/important", response_model=List[ImportantEventResponse])
async def list_important_events(limit: int = 50, offset: int = 0):
    """列出重要事件"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    events = chronicle.list_important_events(limit=limit, offset=offset)
    return [ImportantEventResponse(**{
        "id": e.id, "date_string": e.date_string,
        "date_precision": e.date_precision,
        "event_text": e.event_text, "emotion": e.emotion,
        "created_at": e.created_at, "updated_at": e.updated_at
    }) for e in events]


@router.put("/important/{event_id}", response_model=ImportantEventResponse)
async def update_important_event(event_id: str, request: ImportantEventUpdateRequest):
    """更新重要事件"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    event = chronicle.get_important_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="重要事件不存在")
    updates = {k: v for k, v in {
        "date_string": request.date_string,
        "date_precision": request.date_precision,
        "event_text": request.event_text,
        "emotion": request.emotion
    }.items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    chronicle.update_important_event(event_id, updates)
    updated = chronicle.get_important_event(event_id)
    return ImportantEventResponse(**{
        "id": updated.id, "date_string": updated.date_string,
        "date_precision": updated.date_precision,
        "event_text": updated.event_text, "emotion": updated.emotion,
        "created_at": updated.created_at, "updated_at": updated.updated_at
    })


@router.delete("/important/{event_id}")
async def delete_important_event(event_id: str):
    """删除重要事件"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    success = chronicle.delete_important_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="重要事件不存在")
    return {"message": "已删除", "event_id": event_id}


# ═══════════════════════════════════════════════════════════
# 事件建议
# ═══════════════════════════════════════════════════════════

@router.post("/suggest", response_model=EventSuggestionResponse)
async def suggest_important_event(request: EventSuggestionRequest):
    """评估用户消息是否值得记录为重要事件（两阶段：关键词粗筛 + Flash LLM 评分）"""
    from event_suggestion import evaluate_event_suggestion
    result = await evaluate_event_suggestion(
        user_text=request.user_text,
        flash_llm_func=call_llm_flash_for_text,
        session_id=request.session_id
    )
    return EventSuggestionResponse(
        should_suggest=result.should_suggest,
        confidence=result.confidence,
        suggested_text=result.suggested_text,
        suggested_date=result.suggested_date,
        suggested_emotion=result.suggested_emotion,
        reason=result.reason
    )


@router.post("/suggest/suppress")
async def suppress_event_suggestion(request: EventSuggestionSuppressRequest):
    """用户点击"不再提示"后抑制该 session 的事件建议"""
    from event_suggestion import suppress_session
    suppress_session(request.session_id)
    return {"message": "已抑制", "session_id": request.session_id}


# ═══════════════════════════════════════════════════════════
# 日记
# ═══════════════════════════════════════════════════════════

@router.get("/diary", response_model=List[DiaryEntryResponse])
async def list_diary_entries(limit: int = 30, offset: int = 0):
    """列出日记（按日期倒序）"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    entries = chronicle.list_diary_entries(limit=limit, offset=offset)
    return [DiaryEntryResponse(
        id=e.id, date=e.date, content=e.content,
        summary_ids=e.summary_ids, generated_at=e.generated_at
    ) for e in entries]


@router.get("/diary/latest", response_model=Optional[DiaryEntryResponse])
async def get_latest_diary_entry():
    """获取最新一条日记"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    entry = chronicle.get_latest_diary_entry()
    if not entry:
        return None
    return DiaryEntryResponse(
        id=entry.id, date=entry.date, content=entry.content,
        summary_ids=entry.summary_ids, generated_at=entry.generated_at
    )


@router.get("/memory-boxes/{date}", response_model=MemoryBoxesResponse)
async def get_memory_boxes_by_date(date: str, request: Request, response: Response):
    """Read-only Memory V2 day compact with bounded authoritative sources."""
    from event_chronicle import get_global_chronicle
    from memory_v2.day_projection import load_day_compact_projection
    from context_inspection import require_private_inspection
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    from memory_v2.scene_planner import scene_start_date
    start = scene_start_date()
    if start and date >= start:
        from memory_v2.scene_router import load_current
        from memory_v2.scene_display import timeline_boxes
        material = await load_current(date)
        return MemoryBoxesResponse(date=date, created_at="", boxes=timeline_boxes(material))

    projection = load_day_compact_projection(date)
    if not projection:
        return MemoryBoxesResponse(date=str(date)[:10], created_at="", boxes=[])
    chronicle = get_global_chronicle()
    boxes: List[MemoryBoxResponse] = []
    role_names = {
        "user": "人类伙伴",
        "assistant": "AI",
        "system": "系统",
        "notification": "系统通知",
    }
    for item in projection.get("items", []):
        sources = []
        for message_id in (item.get("message_ids") or [])[:12]:
            message = chronicle.get_message_by_msg_id(str(message_id))
            if not message or not str(message.get("content") or "").strip():
                continue
            role = role_names.get(str(message.get("role") or ""), "AI")
            if message.get("is_wander"):
                role = "AI·漫想"
            elif message.get("is_sentinel"):
                role = "哨兵"
            elif message.get("is_reminder"):
                role = "提醒"
            sources.append(MemoryBoxSourceResponse(
                timestamp=str(message.get("timestamp") or ""),
                role=role,
                content=str(message.get("content") or "").strip()[:1200],
            ))
        boxes.append(MemoryBoxResponse(
            order=int(item.get("order") or 0),
            start_ts=str(item.get("start_ts") or ""),
            end_ts=str(item.get("end_ts") or ""),
            summary=str(item.get("summary") or ""),
            event_count=int(item.get("event_count") or 0),
            source_count=int(item.get("source_count") or 0),
            sources=sources,
        ))
    return MemoryBoxesResponse(
        date=str(projection.get("active_date") or date)[:10],
        created_at=str(projection.get("created_at") or ""),
        boxes=boxes,
    )


@router.get("/diary/{date}", response_model=Optional[DiaryEntryResponse])
async def get_diary_entry_by_date(date: str):
    """按日期获取日记（YYYY-MM-DD）"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    entry = chronicle.get_diary_entry_by_date(date)
    if not entry:
        raise HTTPException(status_code=404, detail="该日期没有日记")
    return DiaryEntryResponse(
        id=entry.id, date=entry.date, content=entry.content,
        summary_ids=entry.summary_ids, generated_at=entry.generated_at
    )


# ═══════════════════════════════════════════════════════════
# 维护
# ═══════════════════════════════════════════════════════════

@router.get("/maintenance/status", response_model=MaintenanceStatusResponse)
async def get_maintenance_status_api():
    """获取维护任务进度（供前端右下角进度条轮询）"""
    from event_chronicle import get_maintenance_status
    status = get_maintenance_status()
    return MaintenanceStatusResponse(
        tasks=[MaintenanceTaskResponse(
            name=t.name, status=t.status, progress=t.progress,
            message=t.message, started_at=t.started_at,
            completed_at=t.completed_at, error=t.error
        ) for t in status.tasks],
        last_run=status.last_run,
        last_success=status.last_success,
        overall_progress=status.overall_progress
    )


@router.post("/maintenance/run")
async def run_maintenance_manual():
    """手动触发每日维护"""
    import sys
    _do_daily_maintenance = (sys.modules.get("main") or sys.modules.get("__main__"))._do_daily_maintenance
    asyncio.create_task(_do_daily_maintenance())
    return {"message": "维护任务已启动，可通过 /events/maintenance/status 查看进度"}
@router.get("/diary/{date}/source-status")
async def get_diary_source_status(date: str):
    from event_chronicle import get_global_chronicle
    from diary_source_audit import status
    try:
        return await asyncio.to_thread(status, get_global_chronicle(), date)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        logger.exception("日记来源核查读取失败: %s", date)
        raise HTTPException(status_code=503, detail="来源读取失败，未判断为无材料")


@router.post("/diary/{date}/source-audit")
async def audit_diary_sources(date: str):
    from event_chronicle import get_global_chronicle
    from diary_source_audit import audit, DiaryAuditConflict
    try:
        saved = await asyncio.to_thread(audit, get_global_chronicle(), date)
        return {"checked_at": saved['checked_at'], "mode": "retrospective"}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DiaryAuditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:
        logger.exception("日记事后来源核查失败: %s", date)
        raise HTTPException(status_code=503, detail="来源核查失败，未修改日记")
