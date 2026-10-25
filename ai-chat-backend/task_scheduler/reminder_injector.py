"""把主动提醒写入会话并推送到前端。

任务调度器只负责决定“该提醒什么”；本模块负责提醒消息的会话边界、持久化、
手机提示和前端直推。这样 main.py 只需要在启动时注册依赖，不承载提醒领域逻辑。
"""

from __future__ import annotations

import logging
import json
from body_admission import ordinary
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional

from time_utils import now_iso
from tool_call_utils import build_tool_summary

logger = logging.getLogger(__name__)


class ReminderInjector:
    """日程/查岗消息的统一注入器。"""

    def __init__(
        self,
        *,
        session_id_provider: Callable[[], Optional[str]],
        set_current_session: Callable[[str], None],
        persist_special_msg: Callable[..., Awaitable[None]],
        record_injected: Callable[[], None],
        wander_connections: Iterable[Any],
        task_broadcast: Callable[[dict], Awaitable[None]],
        emit_notification: Optional[Callable[..., Awaitable[Any]]] = None,
        spawn_async_task: Optional[Callable[[Awaitable[Any]], Any]] = None,
    ) -> None:
        self._session_id_provider = session_id_provider
        self._set_current_session = set_current_session
        self._persist_special_msg = persist_special_msg
        self._record_injected = record_injected
        self._wander_connections = wander_connections
        self._task_broadcast = task_broadcast
        self._emit_notification = emit_notification
        self._spawn_async_task = spawn_async_task

    @ordinary
    async def inject(
        self,
        msg: str,
        is_recheck: bool = False,
        msg_id: str = "",
        is_reminder: bool = True,
        event_type: str = "reminder",
        thinking: str = "",
        model_source: Optional[dict] = None,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """持久化并直推一条主动消息。"""
        from visible_reply import clean_visible_reply
        msg = clean_visible_reply(msg)
        sid = self._session_id_provider() or ""
        if not sid:
            logger.warning("[REMINDER_INJECT] 跳过：无法获取当前会话 ID")
            return False
        self._set_current_session(sid)
        if not msg:
            logger.warning("[REMINDER_INJECT] 跳过：消息为空")
            return False

        try:
            from summary import conversation_manager, ensure_topic_exists, topic_state_machine
            from conversation_manager_v2 import resolve_active_date

            reminder_id = (
                f"reminder_{msg_id}"
                if msg_id
                else f"reminder_{int(datetime.now().timestamp() * 1000)}"
            )
            saved_tool_calls = list(tool_calls or [])
            if saved_tool_calls and self._emit_notification is not None:
                from miband.notices import detach_receipts

                saved_tool_calls = await detach_receipts(
                    sid, reminder_id, saved_tool_calls, self._emit_notification,
                )

            # 提醒是对话的一部分；没有活动话题时先开启一个最小话题边界。
            await ensure_topic_exists(sid)
            status = await topic_state_machine.get_topic_status(sid)
            if not status.get("is_active"):
                from summary import Message

                seed = Message(
                    id=reminder_id,
                    role="assistant",
                    content=msg[:200],
                    timestamp=now_iso(),
                )
                await topic_state_machine.start_new_topic(sid, seed)

            await conversation_manager.add_message(
                sid,
                "assistant",
                msg,
                message_id=reminder_id,
                is_wander=False,
                is_sentinel=False,
                is_reminder=is_reminder,
                event_type=event_type,
                reasoning_content=thinking or None,
                tool_calls=(
                    json.dumps(saved_tool_calls, ensure_ascii=False)
                    if saved_tool_calls else None
                ),
                tool_summary=build_tool_summary(saved_tool_calls),
                active_date=resolve_active_date(sid),
                require_persistence=True,
            )
            await self._persist_special_msg(
                sid,
                reminder_id,
                msg,
                is_reminder=is_reminder,
                event_type=event_type,
                thinking=thinking or None,
                model_source=model_source,
                tool_calls=saved_tool_calls,
            )
            if is_reminder:
                self._record_injected()

            # 手机通知沿用原有 fire-and-forget 语义，避免中继超时阻塞会话注入。
            if self._spawn_async_task:
                self._spawn_async_task(self._push_mobile_notification(msg))
            else:
                await self._push_mobile_notification(msg)
            await self._push_frontend(
                {
                    "type": "reminder_push",
                    "message_id": reminder_id,
                    "message": msg,
                    "session_id": sid,
                    "timestamp": now_iso(),
                    "isReminder": is_reminder,
                    "isRecheck": is_recheck,
                    "eventType": event_type,
                    "thinking": thinking or None,
                    "modelSource": model_source,
                    "toolCalls": saved_tool_calls,
                }
            )
            await self._task_broadcast(
                {
                    "type": "chat_reload",
                    "reason": "reminder_injected",
                    "message_id": reminder_id,
                }
            )
            return True
        except Exception as exc:
            logger.warning(f"[REMINDER_INJECT] 注入失败: {exc}")
            return False

    async def _push_mobile_notification(self, message: str) -> None:
        try:
            from behavior_scheduler.tools import push_mobile_notification

            await push_mobile_notification("AI⏰", message[:120])
        except Exception:
            logger.debug("[REMINDER_INJECT] 手机通知不可用", exc_info=True)

    async def _push_frontend(self, payload: dict) -> None:
        for websocket in list(self._wander_connections):
            try:
                await websocket.send_json(payload)
            except Exception:
                logger.debug("[REMINDER_INJECT] 前端提醒直推失败", exc_info=True)
