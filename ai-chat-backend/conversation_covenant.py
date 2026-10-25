"""Backend-authoritative countdown for the conversation covenant.

The browser renders the countdown, but this service owns activation, expiry and
topic ending.  Runtime state is persisted separately from settings so a page
reload, mobile backgrounding or backend restart cannot silently lose an active
countdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_DEFAULT_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "data", "conversation_covenant_runtime.json"
)
_DEFAULT_DURATION_MINUTES = 30
_MIN_DURATION_MINUTES = 1
_MAX_DURATION_MINUTES = 24 * 60

# 深夜窗口阈值（默认）：>=此小时点"聊到这里"/约定倒计时到期 → 预约次日 00:00 结束（保证"一天一话题"）。
# 之前正常立即结束。00:00 前结束请求延迟到 00:00 后，期间 AI 推送归当天话题，不会产生"当天第二个话题"。
# 实际值从 conversation_covenant 配置的 defer_start_hour 读取（人类伙伴作息可能 21:00 就睡，可调）。
LATE_NIGHT_HOUR = 22


def get_late_night_hour() -> int:
    """读取配置的深夜窗口起始小时（conversation_covenant.defer_start_hour，默认 22）。

    让深夜延时的判断适配用户作息——22:00 是默认，但用户可能 21:00 甚至更早睡。
    """
    try:
        from settings_manager import get_setting
        settings = get_setting("conversation_covenant") or {}
        value = settings.get("defer_start_hour", LATE_NIGHT_HOUR)
        hour = int(value)
        if 0 <= hour <= 23:
            return hour
    except Exception:
        pass
    return LATE_NIGHT_HOUR


def normalize_duration_minutes(value) -> int:
    """把设置值归一为可执行的整数分钟，旧配置缺字段时保持 30 分钟。"""
    if isinstance(value, bool):
        return _DEFAULT_DURATION_MINUTES
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return _DEFAULT_DURATION_MINUTES
    return max(_MIN_DURATION_MINUTES, min(_MAX_DURATION_MINUTES, minutes))


def _time_to_minutes(value: str) -> Optional[int]:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except (TypeError, ValueError):
        pass
    return None


def is_within_window(now: datetime, start_time: str, end_time: str) -> bool:
    """Return whether local ``now`` is inside a possibly cross-midnight window."""
    start = _time_to_minutes(start_time)
    end = _time_to_minutes(end_time)
    if start is None or end is None:
        return False
    current = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= current <= end
    return current >= start or current <= end


class ConversationCovenantService:
    def __init__(
        self,
        *,
        state_path: str = _DEFAULT_STATE_PATH,
        countdown_seconds: int = 30 * 60,
        end_topic_func: Optional[Callable[[str], Awaitable[dict]]] = None,
    ):
        self._state_path = state_path
        self._countdown_seconds = countdown_seconds
        self._end_topic_func = end_topic_func
        self._lock = asyncio.Lock()
        self._timer_task: Optional[asyncio.Task] = None
        self._state = self._empty_state()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "active": False,
            "session_id": "",
            "deadline_epoch": None,
            "activated_at": None,
            "duration_minutes": None,
            "reservation_kind": "",  # "covenant"（约定词）| "manual"（手动"聊到这里"）| ""（无预约）
            "deferred_to_midnight": False,  # 深夜预约到次日 00:00（前端据此显示"次日 00:00 自动结束"而非倒计时）
            "last_end_at": None,
            "last_end_session_id": "",
            "last_end_success": None,
            "last_error": "",
        }

    async def start(self) -> None:
        async with self._lock:
            self._state = self._load_state()
            if self._state.get("active") and self._state.get("session_id"):
                self._schedule_locked()

    async def stop(self) -> None:
        async with self._lock:
            task = self._timer_task
            self._timer_task = None
            if task and task is not asyncio.current_task():
                task.cancel()

    async def observe_message(self, message: str, session_id: str) -> bool:
        """Activate when a persisted covenant word appears in an accepted message."""
        if not message or not session_id:
            return False
        try:
            from settings_manager import get_setting

            settings = get_setting("conversation_covenant") or {}
        except Exception:
            logger.exception("对话约定读取设置失败")
            return False
        word = str(settings.get("word") or "").strip()
        if not word or word not in message:
            return False
        if not is_within_window(
            datetime.now(),
            str(settings.get("start_time") or "00:00"),
            str(settings.get("end_time") or "23:59"),
        ):
            return False
        duration_minutes = normalize_duration_minutes(settings.get("duration_minutes"))
        return await self.activate(
            session_id,
            countdown_seconds=duration_minutes * 60,
            duration_minutes=duration_minutes,
        )

    async def activate(
        self,
        session_id: str,
        *,
        countdown_seconds: Optional[float] = None,
        duration_minutes: Optional[int] = None,
    ) -> bool:
        if not session_id:
            return False
        async with self._lock:
            if self._state.get("active"):
                return False
            # Persist wall-clock epoch, not monotonic time, so restart recovery works.
            import time

            wall_now = time.time()
            duration_seconds = (
                float(countdown_seconds)
                if countdown_seconds is not None
                else float(self._countdown_seconds)
            )
            configured_minutes = (
                normalize_duration_minutes(duration_minutes)
                if duration_minutes is not None
                else max(1, round(duration_seconds / 60))
            )
            deadline_epoch = wall_now + duration_seconds
            # 深夜延迟：倒计时到期时刻在深夜窗口（22:00 后、当天 00:00 前）→ 预约到次日 00:00。
            # 保证"一天一话题"——00:00 前的所有消息（含 AI 推送）归当天话题，00:00 后结束开新话题。
            now_local = datetime.now()
            midnight_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_midnight = midnight_today + timedelta(days=1)
            deferred = False
            if (
                now_local.hour >= get_late_night_hour()
                and midnight_today.timestamp() <= deadline_epoch < tomorrow_midnight.timestamp()
            ):
                deadline_epoch = tomorrow_midnight.timestamp()
                deferred = True
                logger.info("约定倒计时到期在深夜，预约到次日 00:00: session=%s", session_id[:20])
            self._state.update({
                "active": True,
                "session_id": session_id,
                "deadline_epoch": deadline_epoch,
                "activated_at": datetime.now().isoformat(),
                "duration_minutes": configured_minutes,
                "reservation_kind": "covenant",
                "deferred_to_midnight": deferred,
                "last_error": "",
            })
            self._save_state()
            self._schedule_locked()
            logger.info(
                "对话约定倒计时已启动: session=%s duration=%ss",
                session_id[:20],
                duration_seconds,
            )
            return True

    async def cancel(self) -> bool:
        async with self._lock:
            was_active = bool(self._state.get("active"))
            self._state.update({
                "active": False,
                "session_id": "",
                "deadline_epoch": None,
                "activated_at": None,
                "duration_minutes": None,
                "reservation_kind": "",
                "deferred_to_midnight": False,
                "last_error": "",
            })
            task = self._timer_task
            self._timer_task = None
            if task and task is not asyncio.current_task():
                task.cancel()
            self._save_state()
            return was_active

    async def reserve_end(self, session_id: str) -> bool:
        """手动"聊到这里"深夜预约：写入预约，deadline=次日 00:00，不立即结束。

        22:00 后点"聊到这里"时由 topic_router.end_topic 调用。
        00:00 前话题保持活跃（AI 推送归当天话题），00:00 后执行 end_topic，
        保证"一天一话题"（不产生当天第二个话题）。
        """
        if not session_id:
            return False
        async with self._lock:
            if self._state.get("active"):
                # 已有预约/倒计时：保持现状（"聊到这里"不覆盖约定倒计时）。
                # 同 session 视为"已在预约中"，返回 True。
                return self._state.get("session_id") == session_id
            now_local = datetime.now()
            tomorrow_midnight = (now_local + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            self._state.update({
                "active": True,
                "session_id": session_id,
                "deadline_epoch": tomorrow_midnight.timestamp(),
                "activated_at": now_local.isoformat(),
                "duration_minutes": None,
                "reservation_kind": "manual",
                "deferred_to_midnight": True,
                "last_error": "",
            })
            self._save_state()
            self._schedule_locked()
            logger.info(
                "深夜'聊到这里'已预约: session=%s deadline=%s",
                session_id[:20], tomorrow_midnight.isoformat(),
            )
            return True

    async def cancel_reservation(self, session_id: str) -> bool:
        """用户预约后主动发消息 → 取消预约（话题继续活跃）。

        说明用户没真睡，"聊到这里"的结束意图失效；"聊到这里"后的新对话
        不应被概括进旧话题。对 manual（手动）和 covenant（约定词）预约都生效，
        仅当预约 session 与当前 session 匹配时取消。取消后下次再点/再说重新预约。
        """
        async with self._lock:
            if not self._state.get("active") or self._state.get("session_id") != session_id:
                return False
            kind = self._state.get("reservation_kind", "")
            self._state.update({
                "active": False,
                "session_id": "",
                "deadline_epoch": None,
                "activated_at": None,
                "duration_minutes": None,
                "reservation_kind": "",
                "deferred_to_midnight": False,
                "last_error": "",
            })
            task = self._timer_task
            self._timer_task = None
            if task and task is not asyncio.current_task():
                task.cancel()
            self._save_state()
            logger.info("用户发消息，取消话题结束预约 (kind=%s, session=%s)", kind, session_id[:20])
            return True

    async def status(self) -> dict:
        import time

        async with self._lock:
            state = dict(self._state)
            deadline = state.get("deadline_epoch")
            remaining = max(0, math.ceil(float(deadline) - time.time())) if deadline else 0
            # 动态派生延时标记：active 且当前在深夜窗口 → 必然是预约到次日 00:00。
            # （activate/reserve_end 深夜时 deadline 已设为次日 00:00；这里兜底恢复的旧 sidecar，
            #   不必等 _wait_and_expire 跑到 deadline 才设字段。）
            if state.get("active") and deadline and datetime.now().hour >= get_late_night_hour():
                state["deferred_to_midnight"] = True
            state["remaining_seconds"] = remaining
            return state

    def _schedule_locked(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = asyncio.create_task(self._wait_and_expire())

    async def _wait_and_expire(self) -> None:
        import time

        try:
            while True:
                async with self._lock:
                    if not self._state.get("active"):
                        return
                    deadline = float(self._state.get("deadline_epoch") or 0)
                delay = deadline - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                # 深夜延迟防护（双保险，activate/reserve_end 已处理；此处兜底恢复的旧预约）：
                # deadline 在今天内 且 当前时刻在深夜窗口 → 推迟到次日 00:00。
                # deadline 属昨天或更早（跨重启补执行）→ 不推迟，直接执行结束。
                now_local = datetime.now()
                midnight_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
                tomorrow_midnight = midnight_today + timedelta(days=1)
                if (
                    now_local.hour >= get_late_night_hour()
                    and midnight_today.timestamp() <= deadline < tomorrow_midnight.timestamp()
                ):
                    async with self._lock:
                        self._state["deadline_epoch"] = tomorrow_midnight.timestamp()
                        self._state["deferred_to_midnight"] = True
                        self._save_state()
                    continue
                ended = await self._end_active_topic()
                if ended:
                    return
                # Topic cleanup from an earlier round can temporarily hold the
                # authoritative lock. Retry without inventing a successful end.
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("对话约定自动结束异常")
            async with self._lock:
                self._state["last_error"] = str(exc)[:160]
                self._save_state()

    async def _end_active_topic(self) -> bool:
        async with self._lock:
            session_id = str(self._state.get("session_id") or "")
            if not self._state.get("active") or not session_id:
                return True

        end_func = self._end_topic_func
        if end_func is None:
            from routers.topic_router import end_topic

            async def end_func(sid: str) -> dict:
                return await end_topic(sid, reason="covenant")

        result = await end_func(session_id)
        success = bool(result and result.get("success"))
        if not success and result and result.get("busy"):
            try:
                from summary import topic_state_machine

                topic_status = await topic_state_machine.get_topic_status(session_id)
                if topic_status and not topic_status.get("is_active", True):
                    success = True
            except Exception:
                pass
        if not success:
            async with self._lock:
                self._state["last_error"] = str((result or {}).get("message") or "话题结束暂未成功")[:160]
                self._save_state()
            return False

        async with self._lock:
            self._state.update({
                "active": False,
                "session_id": "",
                "deadline_epoch": None,
                "activated_at": None,
                "duration_minutes": None,
                "reservation_kind": "",
                "deferred_to_midnight": False,
                "last_end_at": datetime.now().isoformat(),
                "last_end_session_id": session_id,
                "last_end_success": True,
                "last_error": "",
            })
            self._timer_task = None
            self._save_state()
        logger.info("对话约定倒计时结束，已自动结束话题: session=%s", session_id[:20])
        return True

    def _load_state(self) -> dict:
        state = self._empty_state()
        try:
            with open(self._state_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                state.update(loaded)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("对话约定运行态读取失败，使用空状态")
        return state

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        tmp_path = self._state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self._state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._state_path)


_service: Optional[ConversationCovenantService] = None


def get_conversation_covenant_service() -> ConversationCovenantService:
    global _service
    if _service is None:
        _service = ConversationCovenantService()
    return _service


async def observe_accepted_message(message: str, session_id: str, channel: str) -> bool:
    """Shared HTTP/WS integration boundary for accepted chat messages.

    Chat transports only report the canonical persisted text. Detection,
    failure isolation and logging stay inside the covenant module so main.py
    does not grow a second copy for every transport.
    """
    if not message or not session_id:
        return False
    try:
        return await get_conversation_covenant_service().observe_message(message, session_id)
    except Exception:
        logger.exception("%s 对话约定检测失败", channel)
        return False
