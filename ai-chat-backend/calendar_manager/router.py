"""
日历管理器 - FastAPI 路由

REST API + WebSocket 端点
前缀: /api/calendar
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from .models import CalendarEventCreateRequest, CalendarEventUpdateRequest
from .manager import get_global_calendar_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def _now_iso() -> str:
    return datetime.now().isoformat()


# ── REST 端点 ─────────────────────────────────────────

@router.get("/events")
async def list_events(
    year: Optional[int] = Query(None),
    pending: bool = Query(False),
):
    """列出日历事件。pending=true 时返回待确认的纪念日"""
    mgr = get_global_calendar_manager()
    if pending:
        events = mgr.get_pending_confirmations()
    else:
        events = mgr.list_events(year=year)
    return [e.model_dump() for e in events]


@router.post("/events")
async def create_event(req: CalendarEventCreateRequest):
    """创建日历事件"""
    mgr = get_global_calendar_manager()
    event = mgr.create_event(req)
    await mgr.broadcast_update()
    return event.model_dump()


@router.get("/events/{event_id}")
async def get_event(event_id: str):
    """获取单个日历事件"""
    mgr = get_global_calendar_manager()
    event = mgr.get_event(event_id)
    if event is None:
        return {"error": "事件不存在"}, 404
    return event.model_dump()


@router.put("/events/{event_id}")
async def update_event(event_id: str, req: CalendarEventUpdateRequest):
    """更新日历事件"""
    mgr = get_global_calendar_manager()
    updates = req.model_dump(exclude_none=True)
    event = mgr.update_event(event_id, updates)
    if event is None:
        return {"error": "事件不存在"}, 404
    await mgr.broadcast_update()
    return event.model_dump()


@router.delete("/events/{event_id}")
async def delete_event(event_id: str):
    """删除日历事件"""
    mgr = get_global_calendar_manager()
    success = mgr.delete_event(event_id)
    if not success:
        return {"error": "事件不存在"}, 404
    await mgr.broadcast_update()
    return {"success": True}


# ── 月度查询 ─────────────────────────────────────────

@router.get("/month/{year}/{month}")
async def get_month_events(year: int, month: int):
    """获取指定月份每天的事件映射"""
    mgr = get_global_calendar_manager()
    events_by_day = mgr.get_month_events(year, month)
    return {
        "year": year,
        "month": month,
        "days_with_events": {
            str(day): [e.model_dump() for e in events]
            for day, events in events_by_day.items()
        },
    }


# ── 扫描 ─────────────────────────────────────────────

@router.post("/scan")
async def trigger_scan(days_absent: int = 0):
    """手动触发日历扫描（登录时调用）"""
    mgr = get_global_calendar_manager()
    result = await mgr.scan_today_events(days_absent=days_absent)
    if result:
        # 异步推送到前端
        await mgr.broadcast_scan_result(result)
    return result.model_dump() if result else {"events_found": 0}


# ── 休息日 ───────────────────────────────────────────

@router.post("/rest-days/sync")
async def sync_rest_days(year: int = None):
    """手动同步指定年份的休息日（中国法定节假日+双休）"""
    mgr = get_global_calendar_manager()
    count = mgr.sync_rest_days(year)
    await mgr.broadcast_update()
    return {"synced": count}

@router.get("/rest-day/today")
async def check_rest_day_today():
    """查询今天是否为休息日"""
    from .database import is_rest_day_today
    return {"is_rest_day": is_rest_day_today()}


# ── 生理期查询 ───────────────────────────────────────

@router.get("/period/active")
async def check_period_active():
    """查询当前是否处于生理期"""
    mgr = get_global_calendar_manager()
    active = mgr.is_period_active_today()
    return {"active": active}


# ── 系统状态 ─────────────────────────────────────────

@router.get("/state")
async def get_state():
    """获取系统状态（last_active_at 等）"""
    mgr = get_global_calendar_manager()
    return {
        "last_active_at": mgr.get_last_active_at(),
    }


@router.put("/state")
async def update_state(last_active_at: Optional[str] = None):
    """更新系统状态"""
    mgr = get_global_calendar_manager()
    mgr.set_last_active_at(last_active_at)
    return {"success": True}


# ── 年度归档 ─────────────────────────────────────────

@router.post("/archive")
async def trigger_archive():
    """手动触发年度归档（测试用）"""
    mgr = get_global_calendar_manager()
    result = mgr.archive_old_events()
    return result


# ── WebSocket ────────────────────────────────────────

@router.websocket("/ws")
async def calendar_websocket(ws: WebSocket):
    """日历实时推送 WebSocket"""
    mgr = get_global_calendar_manager()
    await ws.accept()
    mgr.add_connection(ws)
    try:
        while True:
            # 保持连接，接收客户端的 keepalive 或忽略消息
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        mgr.remove_connection(ws)
