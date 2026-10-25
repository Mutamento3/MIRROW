"""手机中继事件与任务调度器之间的协议桥接。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_checkup_alarm_event(message: dict, websocket: Any) -> None:
    """处理手机本地闹钟状态，并回传 ACK。

    手机端以 ACK 是否成功决定本地事件是否可以从持久队列移除；因此无论
    调度器返回成功还是业务失败，都只在这里生成统一的协议响应。
    """
    try:
        from task_scheduler import get_global_task_manager

        task_manager = get_global_task_manager()
        result = (
            await task_manager.handle_mobile_alarm_event(message)
            if task_manager
            else {"success": False}
        )
        ack = {
            "type": "checkup_alarm_event_ack",
            "event_key": message.get("event_key", ""),
            "event_id": message.get("event_id", ""),
            "state": message.get("state", ""),
            "success": bool(result.get("success")) if isinstance(result, dict) else False,
        }
        await websocket.send_json(ack)
    except Exception as exc:
        logger.warning(f"[CHECKUP] 手机闹钟事件同步失败: {exc}")

