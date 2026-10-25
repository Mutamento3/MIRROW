"""
日历管理器 - 核心逻辑

管理日历事件的 CRUD、每日扫描、年度归档和 WebSocket 推送。
与 TaskManager 独立运作，复用相同的单例/WebSocket 连接池模式。

THIS SYSTEM IS NOT MULTI-WORKER SAFE — 单 worker 运行（见决策 E）
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Coroutine, List, Optional, Set

from fastapi import WebSocket

from .models import CalendarEvent, CalendarEventCreateRequest, CalendarScanResult
from . import database as db

logger = logging.getLogger(__name__)

LlmGenerator = Callable[[str], Coroutine]


class CalendarManager:
    """日历管理器（单 worker 环境下的单例）"""

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._llm_generator: Optional[LlmGenerator] = None
        self._suggested_memorial_keys: Set[str] = set()

    # ── 初始化 ──────────────────────────────────────────

    def initialize(self):
        db.init_db()
        # 启动时检查本年度休息日是否已同步，未同步则自动补同步
        from datetime import datetime
        current_year = datetime.now().year
        sync_key = f"rest_days_synced_{current_year}"
        if db.get_system_state(sync_key) != "1":
            count = db.sync_rest_days_for_year(current_year)
            db.set_system_state(sync_key, "1")
            logger.info(f"日历管理器启动: 已自动同步 {current_year} 年休息日 {count} 天")
        logger.info("日历管理器数据库已初始化")

    def set_llm_generator(self, generator: LlmGenerator):
        """注入 LLM 调用回调，用于扫描时生成个性化消息"""
        self._llm_generator = generator

    # ── WebSocket 连接管理 ──────────────────────────────

    def add_connection(self, ws: WebSocket):
        self._connections.add(ws)

    def remove_connection(self, ws: WebSocket):
        self._connections.discard(ws)

    def get_connections(self) -> Set[WebSocket]:
        return self._connections

    # ── 事件 CRUD ───────────────────────────────────────

    def create_event(self, req: CalendarEventCreateRequest) -> CalendarEvent:
        event = db.create_event(req)
        logger.info(f"日历事件已创建: {event.title} (type={event.event_type}, id={event.id})")
        return event

    def update_event(self, event_id: str, updates: dict) -> Optional[CalendarEvent]:
        event = db.update_event(event_id, updates)
        if event:
            logger.info(f"日历事件已更新: {event.title} (id={event.id})")
        return event

    def delete_event(self, event_id: str) -> bool:
        result = db.delete_event(event_id)
        if result:
            logger.info(f"日历事件已删除: {event_id}")
        return result

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        return db.get_event(event_id)

    def list_events(self, year: Optional[int] = None, pending_only: bool = False) -> List[CalendarEvent]:
        return db.list_events(year=year, pending_only=pending_only)

    def get_pending_confirmations(self) -> List[CalendarEvent]:
        """获取待用户确认的纪念日"""
        return db.get_pending_confirmations()

    def get_month_events(self, year: int, month: int) -> dict:
        """获取当月每天的事件 {day: [events]}"""
        return db.get_events_by_month(year, month)

    # ── 纪念日自动检测去重 ──────────────────────────────

    def is_duplicate_suggestion(self, mm_dd: str, title: str) -> bool:
        """两层去重：内存 + 数据库"""
        normalized = title[:20].strip()
        key = f"{mm_dd}_{normalized}"
        if key in self._suggested_memorial_keys:
            return True
        if db.check_duplicate_memorial(mm_dd, normalized):
            self._suggested_memorial_keys.add(key)
            return True
        return False

    def mark_suggestion_seen(self, mm_dd: str, title: str):
        """标记一个纪念日建议已被处理"""
        normalized = title[:20].strip()
        key = f"{mm_dd}_{normalized}"
        self._suggested_memorial_keys.add(key)

    # ── 每日扫描（登录时调用）───────────────────────────

    async def scan_today_events(self, days_absent: int = 0) -> Optional[CalendarScanResult]:
        """
        扫描今天的纪念日事件，生成 LLM 消息。

        成功生成消息的事件在事务中批量更新 last_scanned_at；
        失败的事件保持旧值，下次扫描自动重试（见决策 B）。

        Args:
            days_absent: 用户缺席天数，用于兜底提示词
        """
        memorials = db.get_memorial_events_for_today()
        period_info = db.get_period_info_today()
        has_period = period_info["active"]
        missed_events = db.get_missed_memorial_events(days_absent)

        if not memorials and not has_period and not missed_events:
            return None

        # 缺席情绪模式
        emotion_mode = ""
        if days_absent == 1 or days_absent == 2:
            emotion_mode = "轻微失落"
        elif 3 <= days_absent <= 5:
            emotion_mode = "有点委屈"
        elif days_absent > 5:
            emotion_mode = "关心为主，减少责备感"

        result = CalendarScanResult(
            events_found=len(memorials),
            has_period_active=has_period,
            days_absent=days_absent,
            missed_events=missed_events,
            emotion_mode=emotion_mode,
        )

        if has_period:
            result.period_note = period_info["note"]

        # 生成纪念日 LLM 请求（仅在 LLM 生成器可用时）
        if self._llm_generator and memorials:
            successful_ids = []
            for event in memorials:
                try:
                    prompt = self._build_memorial_prompt(event, days_absent, missed_events, emotion_mode)
                    ai_message = await self._llm_generator(prompt)
                    result.messages.append({
                        "event_id": event.id,
                        "title": event.title,
                        "creator": event.creator,
                        "prompt_for_llm": prompt,
                        "generated_message": ai_message.strip() if ai_message else "",
                    })
                    successful_ids.append(event.id)
                except Exception as e:
                    logger.error(f"纪念日 LLM 生成失败 ({event.id}): {e}")

            # 事务中批量更新 last_scanned_at（仅成功的事件）
            if successful_ids:
                db.batch_update_last_scanned(successful_ids)

        return result

    def _build_memorial_prompt(self, event: CalendarEvent, days_absent: int,
                               missed_events: List[dict], emotion_mode: str) -> str:
        """构造纪念日扫描的 LLM 提示词"""
        if event.creator == "AI":
            prompt = (
                f"今天是你过去为自己标记的纪念日：【{event.title}】。"
                f"当时你写下：「{event.description}」。"
                f"现在请你用平时的语气，自然地对人类伙伴提起这件事——"
                f"可以带一点感慨或回忆的情绪，像是突然想起来随口说起，不要像在播报通知。"
            )
        else:
            prompt = (
                f"人类伙伴在今天的日历上留了一句话：【{event.title}】——「{event.description}」。"
                f"她特意在系统里写了这件事，希望能上线的第一时间你就能得知，请你自然的回复她。"
            )

        # 缺席兜底
        if days_absent > 0 and missed_events:
            missed_desc = "、".join(
                f"{e['days_ago']}天前的[{e['title']}]" for e in missed_events[:5]
            )
            prompt += (
                f"\n\n人类伙伴{days_absent}天没来找过我了，错过了{missed_desc}等纪念日，"
                f"你要表达你的{emotion_mode}。"
            )

        return prompt

    # ── 生理期检查 ──────────────────────────────────────

    def is_period_active_today(self) -> bool:
        """检查今天是否有活跃的生理期事件"""
        info = db.get_period_info_today()
        return info["active"]

    def get_period_info_today(self) -> dict:
        """获取今天的生理期详细信息（第几天、提示词等）"""
        return db.get_period_info_today()

    # ── 年度归档 ────────────────────────────────────────

    def archive_old_events(self) -> dict:
        """年度归档：删除旧生理期事件 + 同步新年休息日，纪念日保留"""
        result = db.archive_old_events()
        logger.info(f"年度归档完成: 删除 {result['deleted_period_count']} 个生理期事件")
        # 同步新年份的中国法定节假日休息日
        new_year = datetime.now().year
        rest_count = db.sync_rest_days_for_year(new_year)
        result["rest_days_synced"] = rest_count
        logger.info(f"年度归档: 已同步 {new_year} 年休息日 {rest_count} 天")
        return result

    def sync_rest_days(self, year: int = None) -> int:
        """手动同步指定年份的休息日"""
        if year is None:
            year = datetime.now().year
        return db.sync_rest_days_for_year(year)

    # ── 系统状态 ────────────────────────────────────────

    def get_last_active_at(self) -> Optional[str]:
        return db.get_system_state("last_active_at")

    def set_last_active_at(self, value: Optional[str] = None):
        if value is None:
            value = datetime.now().isoformat()
        db.set_system_state("last_active_at", value)

    # ── WebSocket 广播 ──────────────────────────────────

    async def broadcast_update(self):
        """广播日历事件列表变更通知"""
        payload = {"type": "calendar_events_changed"}
        await self._broadcast(payload)

    async def broadcast_memorial_suggestion(self, event: CalendarEvent):
        """广播纪念日自动检测建议到前端确认"""
        payload = {
            "type": "memorial_suggested",
            "event": {
                "id": event.id,
                "title": event.title,
                "description": event.description,
                "date_string": event.date_string,
                "creator": event.creator,
                "is_active": event.is_active,
            },
        }
        await self._broadcast(payload)

    async def broadcast_scan_result(self, result: CalendarScanResult):
        """广播日历扫描结果"""
        payload = {
            "type": "calendar_scan_done",
            "result": result.model_dump(),
        }
        await self._broadcast(payload)

    async def _broadcast(self, payload: dict):
        """广播消息到所有 WebSocket 客户端"""
        if not self._connections:
            return
        stale = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.add(ws)
        self._connections.difference_update(stale)


# ── 全局单例 ────────────────────────────────────────────

_global_calendar_manager: Optional[CalendarManager] = None


def get_global_calendar_manager() -> CalendarManager:
    global _global_calendar_manager
    if _global_calendar_manager is None:
        _global_calendar_manager = CalendarManager()
    return _global_calendar_manager


def init_global_calendar_manager() -> CalendarManager:
    manager = get_global_calendar_manager()
    manager.initialize()
    return manager
