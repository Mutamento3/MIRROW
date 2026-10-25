"""
刷手机陪聊 — 推送回调

负责将 PhoneBrowser 生成的陪刷消息持久化并推送到前端。
参照 _push_wander_message / _push_sentinel_message 的推送模式：

  1. event_id 去重
  2. session_id 三级回退（_latest_session_id → sessions_index.json）
  3. conversation_manager.add_message() → SQLite
  4. _persist_special_msg() → Session JSON
  5. wander_connections 广播 phone_browse_push（含完整消息内容）
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── 模块级依赖（由 main.py startup 注入） ──
_conversation_manager = None
_wander_connections: Optional[set] = None
_session_id_provider: Optional[Callable] = None      # () -> str | None
_persist_special_msg_func: Optional[Callable] = None  # async (session_id, msg_id, content, **kwargs)
_now_iso_func: Optional[Callable] = None              # () -> str  ISO 时间戳
_mobile_relay_func: Optional[Callable] = None         # async (request_id, tool, params) -> dict

# ── 去重 ──
_pushed_ids: set = set()
_pushed_ids_lock = asyncio.Lock()


def setup_phone_browse_push(
    conversation_manager,
    wander_connections: set,
    session_id_provider: Callable,
    persist_special_msg_func: Callable,
    now_iso_func: Callable,
    mobile_relay_func: Optional[Callable] = None,
):
    """由 main.py startup 调用一次，注入依赖"""
    global _conversation_manager, _wander_connections
    global _session_id_provider, _persist_special_msg_func, _now_iso_func
    global _mobile_relay_func
    _conversation_manager = conversation_manager
    _wander_connections = wander_connections
    _session_id_provider = session_id_provider
    _persist_special_msg_func = persist_special_msg_func
    _now_iso_func = now_iso_func
    _mobile_relay_func = mobile_relay_func


async def push_phone_browse_message(message: str, thinking: Optional[str] = None):
    """推送陪刷消息：去重 → session 解析 → SQLite → Session JSON → WS 广播"""
    from visible_reply import clean_visible_reply
    message = clean_visible_reply(message)
    if not message:
        return

    # ── 0. 生成 event_id ──
    event_id = f"pb_{int(datetime.now().timestamp() * 1000)}"

    # ── 1. 去重 ──
    async with _pushed_ids_lock:
        if event_id in _pushed_ids:
            logger.info(f"PhoneBrowse push: 事件 {event_id} 已推送过，跳过")
            return
        _pushed_ids.add(event_id)
        if len(_pushed_ids) > 200:
            oldest = list(_pushed_ids)[:100]
            for k in oldest:
                _pushed_ids.discard(k)

    logger.info(f"[PHONE_BROWSE_PUSH] 开始推送 event={event_id} msg={message[:60]}...")

    # ── 2. 解析 target_session（三级回退） ──
    target_session = None
    if _session_id_provider:
        target_session = _session_id_provider()

    if not target_session:
        # 回退：sessions_index.json
        try:
            _idx_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "sessions", "sessions_index.json"
            )
            if os.path.exists(_idx_path):
                with open(_idx_path, "r", encoding="utf-8") as _f:
                    _idx = json.load(_f)
                target_session = _idx.get("current_session_id") if isinstance(_idx, dict) else None
                logger.info(f"[PHONE_BROWSE_PUSH] 从 sessions_index 获取 session: {target_session}")
        except Exception as e:
            logger.warning(f"[PHONE_BROWSE_PUSH] sessions_index 读取失败: {e}")

    if not target_session:
        logger.warning("[PHONE_BROWSE_PUSH] FAIL: 无活跃会话，消息丢弃")
        return

    msg_id = f"phone_browse_{event_id}"
    logger.info(f"[PHONE_BROWSE_PUSH] session={target_session} msg_id={msg_id}")

    # ── 3. 写入 conversation_manager（SQLite） ──
    try:
        if _conversation_manager:
            await _conversation_manager.add_message(
                target_session, "assistant", message,
                message_id=msg_id,
                is_wander=False, is_sentinel=False, is_reminder=False,
            )
            logger.info(f"[PHONE_BROWSE_PUSH] conversation_manager OK")
    except Exception as e:
        logger.warning(f"[PHONE_BROWSE_PUSH] conversation_manager FAIL: {e}")

    # ── 4. 写入 session_manager（Session JSON，前端显示） ──
    try:
        if _persist_special_msg_func:
            await _persist_special_msg_func(
                target_session, msg_id, message,
                is_wander=False, is_sentinel=False, is_reminder=False,
                thinking=thinking,
            )
            logger.info(f"[PHONE_BROWSE_PUSH] persist_special_msg OK")
    except Exception as e:
        logger.warning(f"[PHONE_BROWSE_PUSH] persist_special_msg FAIL: {e}")

    # ── 5. WebSocket 广播完整内容 ──
    if not _wander_connections:
        logger.info("[PHONE_BROWSE_PUSH] 无 wander_connections，跳过 WS 推送")
        return

    now_iso = _now_iso_func() if _now_iso_func else datetime.now().isoformat()
    ws_payload = {
        "type": "phone_browse_push",
        "message": message,
        "timestamp": now_iso,
        "eventId": event_id,
        "thinking": thinking or "",
    }

    stale = set()
    for ws in list(_wander_connections):
        try:
            await ws.send_json(ws_payload)
        except Exception:
            stale.add(ws)

    if stale:
        _wander_connections.difference_update(stale)
        logger.info(f"[PHONE_BROWSE_PUSH] 清理 {len(stale)} 个断开的 WS 连接")

    # ── 6. 手机通知栏（用户不在 MIRROW 时也能看到）──
    if _mobile_relay_func:
        try:
            notify_body = message[:100] + ("..." if len(message) > 100 else "")
            await _mobile_relay_func(
                str(event_id) + "_notify",
                "mobile_notify",
                {"title": "AI 在看你的手机", "body": notify_body},
            )
            logger.info(f"[PHONE_BROWSE_PUSH] mobile_notify 已发送")
        except Exception as e:
            logger.warning(f"[PHONE_BROWSE_PUSH] mobile_notify 失败（非致命）: {type(e).__name__}: {e}")

    logger.info(f"[PHONE_BROWSE_PUSH] 推送完成: {message[:60]}...")
