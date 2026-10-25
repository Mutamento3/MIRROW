"""统一消息路由 — 所有自主消息推送的唯一入口

收敛 _push_sentinel_message / _push_wander_message / _persist_special_msg
的重复逻辑。wander_connections 封装为 ConnectionPool。
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Set

logger = logging.getLogger(__name__)


class ConnectionPool:
    """WebSocket 连接池，替代散落的 wander_connections set()"""

    def __init__(self):
        self._connections: Set[Any] = set()

    def register(self, ws):
        self._connections.add(ws)

    def unregister(self, ws):
        self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)

    async def broadcast(self, payload: dict):
        """广播 JSON 消息到所有连接。断开连接自动清理。"""
        dead = set()
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._connections -= dead


# 全局连接池（替代 main.py 中的 wander_connections set）
_pool = ConnectionPool()
# 推送回调（由 main.py 注入，用于持久化存储）
_push_callback: Optional[callable] = None
# 手机通知推送回调
_mobile_notify: Optional[callable] = None
# 已推送事件去重（sentinel 用）
_pushed_sentinel_ids: Dict[str, float] = {}
_MAX_PUSHED_IDS = 200


def get_pool() -> ConnectionPool:
    return _pool


def set_push_callback(cb):
    global _push_callback
    _push_callback = cb


def set_mobile_notify(cb):
    global _mobile_notify
    _mobile_notify = cb


async def push_autonomous_message(
    msg_type: str,
    content: str,
    metadata: Dict[str, Any] = None,
) -> bool:
    """统一推送入口。

    Args:
        msg_type: 'sentinel' | 'wander' | 'reminder'
        content: 消息文本
        metadata: {rule_id, priority, event_id, ...}
    """
    metadata = metadata or {}
    event_id = metadata.get("event_id", "")

    # 去重
    if event_id and event_id in _pushed_sentinel_ids:
        return False

    if event_id:
        _pushed_sentinel_ids[event_id] = datetime.now().timestamp()
        if len(_pushed_sentinel_ids) > _MAX_PUSHED_IDS:
            oldest = sorted(_pushed_sentinel_ids.items(), key=lambda x: x[1])[:100]
            for k, _ in oldest:
                _pushed_sentinel_ids.pop(k, None)

    # 持久化存储
    if _push_callback:
        try:
            await _push_callback(msg_type, content, metadata)
        except Exception as e:
            logger.error(f"MessageRouter 持久化失败: {e}")

    # WebSocket 广播（根据类型决定 channel）
    ws_payload = {
        "type": f"{msg_type}_push",
        "message": content,
        "timestamp": datetime.now().isoformat(),
        **{k: v for k, v in metadata.items() if k not in ("event_id",)},
    }
    await _pool.broadcast(ws_payload)

    # 手机通知
    if _mobile_notify:
        try:
            label = {"sentinel": "AI🛡️", "wander": "AI💭", "reminder": "AI⏰"}.get(msg_type, "AI")
            await _mobile_notify(label, content[:120])
        except Exception:
            pass

    return True
