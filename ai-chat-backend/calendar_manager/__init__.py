"""
日历管理器 - 管理纪念日和生理期标记

提供日历事件的 CRUD、每日扫描、年度归档和 WebSocket 推送。
与 task_scheduler 独立运作，前端在同一面板中上下布局展示。
"""
from .models import CalendarEvent, CalendarEventCreateRequest, CalendarEventUpdateRequest, CalendarScanResult
from .manager import CalendarManager, get_global_calendar_manager, init_global_calendar_manager
from .router import router

__all__ = [
    "CalendarEvent",
    "CalendarEventCreateRequest",
    "CalendarEventUpdateRequest",
    "CalendarScanResult",
    "CalendarManager",
    "get_global_calendar_manager",
    "init_global_calendar_manager",
    "router",
]
