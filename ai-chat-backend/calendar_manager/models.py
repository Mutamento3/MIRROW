"""
日历管理器 - 数据模型
"""
from pydantic import BaseModel
from typing import Optional, List


class CalendarEvent(BaseModel):
    """日历事件（纪念日或生理期）"""
    id: str
    title: str
    description: str = ""
    event_type: str  # "memorial" | "period" | "rest_day"
    date_string: str  # 统一 YYYY-MM-DD，纪念日用 0000 年份如 0000-04-28
    creator: str = "人类伙伴"  # "AI" | "人类伙伴"
    emotion: str = ""
    last_scanned_at: Optional[str] = None
    is_active: bool = True  # 自动检测创建的纪念日初始为 False，待用户确认后激活
    created_at: str
    updated_at: str


class CalendarEventCreateRequest(BaseModel):
    """创建日历事件请求"""
    title: str
    description: str = ""
    event_type: str  # "memorial" | "period" | "rest_day"
    date_string: str  # YYYY-MM-DD，纪念日年份部分会被自动替换为 0000
    creator: str = "人类伙伴"
    emotion: str = ""
    is_active: bool = True  # 手动创建默认激活，自动检测传入 False


class CalendarEventUpdateRequest(BaseModel):
    """更新日历事件请求"""
    title: Optional[str] = None
    description: Optional[str] = None
    date_string: Optional[str] = None
    emotion: Optional[str] = None
    is_active: Optional[bool] = None


class CalendarScanResult(BaseModel):
    """日历扫描结果"""
    events_found: int = 0
    messages: List[dict] = []  # [{event_id, title, creator, prompt_for_llm, ...}]
    has_period_active: bool = False
    period_note: Optional[str] = None
    days_absent: int = 0
    missed_events: List[dict] = []  # [{title, days_ago, date_string}, ...]
    emotion_mode: str = ""
