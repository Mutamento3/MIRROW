"""Private GET-only diagnostics: no inference or mutation on inspection."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request, Response
from context_inspection import require_private_inspection
from memory_v2.conversation_source import ConversationSource
from . import runtime
from .diagnostics import review_day, status
from .store import ShadowStore
from .sentinel_facts import SentinelDailySource

router = APIRouter(prefix="/api/habit-radar", tags=["habit-radar-shadow"])


@router.get("/frequency")
def get_frequency(request: Request, response: Response):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        from memory_v2.day_projection import _configured_paths
        from .frequency import project_signals
        source = ConversationSource(runtime.AUTHORITY_PATH, canonical_sessions_only=True)
        store = ShadowStore(runtime.DB_PATH)
        signals = tuple(signal for row in store.days()[:30] for signal in store.signals_for(row["active_date"]))
        return project_signals(signals, source, _configured_paths())
    except Exception:
        raise HTTPException(503, "频次诊断暂不可用") from None


@router.get("/status")
def get_status(request: Request, response: Response):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        source = ConversationSource(runtime.AUTHORITY_PATH, canonical_sessions_only=True)
        sentinel = SentinelDailySource(runtime.ROOT / "silicon_perception/data/silicon_perception.db")
        from memory_v2.scene_digest_runtime import runtime_status as scene_status
        return {**runtime.runtime_status(), **status(ShadowStore(runtime.DB_PATH), source, sentinel=sentinel),
                "scene_digestion": scene_status()}
    except Exception:
        raise HTTPException(503, "习惯雷达影子诊断暂不可用") from None


@router.get("/days/{active_date}")
def get_day(active_date: str, request: Request, response: Response, offset: int = Query(0, ge=0, le=10000),
            limit: int = Query(40, ge=1, le=80)):
    require_private_inspection(request)
    response.headers["Cache-Control"] = "no-store"
    try:
        if date.fromisoformat(active_date).isoformat() != active_date:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from None
    try:
        source = ConversationSource(runtime.AUTHORITY_PATH, canonical_sessions_only=True)
        return review_day(ShadowStore(runtime.DB_PATH), source, active_date, offset=offset, limit=limit)
    except Exception:
        raise HTTPException(503, "这天的影子复核材料暂不可用") from None
