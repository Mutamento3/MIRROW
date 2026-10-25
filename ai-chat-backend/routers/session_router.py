"""会话管理 API 路由 — 从 main.py 提取"""

import asyncio
import logging
import time as _time
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from message_search import MessageSearchType

from session_manager import get_global_session_manager, ThinkingStep
from routers.shared_models import (
    FrontendMessage, FrontendSession, SessionListResponse,
    SessionCreateRequest, SessionUpdateRequest, ClearSessionsRequest,
    ToolCallRecord, convert_msg_to_frontend,
)
from shared_state import (
    get_latest_session_id, set_latest_session_id,
    get_session_op_lock,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}/chat-turn-status")
async def get_chat_turn_status(
    session_id: str,
    user_message_id: str = Query(..., min_length=1, max_length=200),
):
    """Body-free lifecycle projection of the shared HTTP/WS turn guard."""
    from chat_turn_guard import get_chat_turn_guard
    return {
        "session_id": session_id,
        "user_message_id": user_message_id,
        "status": get_chat_turn_guard().status(session_id, user_message_id),
    }

# per-session PUT 去抖状态


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

async def _fill_missing_thinking(session_id: str, messages: list) -> None:
    """会话消息缺少 thinkingChain 时，从 conversation_manager 补齐 reasoning_content。"""
    if not session_id or not messages:
        return
    assistant_msgs = []
    for m in messages:
        role = getattr(m, 'role', '') if hasattr(m, 'role') else m.get('role', '') if isinstance(m, dict) else ''
        if role != 'assistant':
            continue
        has_think = (
            (hasattr(m, 'thinkingChain') and m.thinkingChain)
            or (hasattr(m, 'reasoning_content') and m.reasoning_content)
            or (isinstance(m, dict) and (m.get('thinkingChain') or m.get('reasoning_content')))
        )
        if not has_think:
            assistant_msgs.append(m)
    if not assistant_msgs:
        return
    try:
        from summary import conversation_manager as _cm
        conv_msgs = await _cm.get_unsummarized_messages(session_id)
        if not conv_msgs:
            import os as _os
            # __file__ 在 routers/session_router.py → 往上三级到 ai-chat-backend/
            logs_root = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "conversations", "logs")
            alt_sids = set()
            if _os.path.isdir(logs_root):
                for _d in _os.listdir(logs_root):
                    _dd = _os.path.join(logs_root, _d)
                    if _os.path.isdir(_dd) and len(_d) == 10:
                        for _f in _os.listdir(_dd):
                            if _f.endswith('.json'):
                                alt_sids.add(_f[:-5])
            for alt_sid in alt_sids:
                if alt_sid == session_id:
                    continue
                conv_msgs = await _cm.get_unsummarized_messages(alt_sid)
                if conv_msgs:
                    break
    except Exception:
        return
    if not conv_msgs:
        return
    by_epoch: dict = {}
    for cm in conv_msgs:
        if getattr(cm, 'role', '') != 'assistant':
            continue
        rc = getattr(cm, 'reasoning_content', None)
        if not rc:
            continue
        ts = getattr(cm, 'timestamp', '')
        if not ts:
            continue
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            if ts.endswith('Z'):
                epoch = _dt.fromisoformat(ts[:-1]).replace(tzinfo=_tz.utc).timestamp()
            elif 'T' in ts:
                dt = _dt.fromisoformat(ts)
                if dt.tzinfo is not None:
                    epoch = dt.timestamp()
                else:
                    epoch = dt.replace(tzinfo=_tz.utc).timestamp()
            else:
                epoch = _dt.strptime(ts, '%m/%d/%Y %H:%M:%S').replace(tzinfo=_tz(_td(hours=8))).timestamp()
        except Exception:
            continue
        by_epoch.setdefault(int(epoch), []).append(rc)
    if not by_epoch:
        return
    for m in assistant_msgs:
        ts = getattr(m, 'timestamp', '') if hasattr(m, 'timestamp') else m.get('timestamp', '') if isinstance(m, dict) else ''
        if not ts:
            continue
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            if ts.endswith('Z'):
                epoch = _dt.fromisoformat(ts[:-1]).replace(tzinfo=_tz.utc).timestamp()
            elif 'T' in ts:
                dt = _dt.fromisoformat(ts)
                if dt.tzinfo is not None:
                    epoch = dt.timestamp()
                else:
                    epoch = dt.replace(tzinfo=_tz.utc).timestamp()
            else:
                epoch = _dt.strptime(ts, '%m/%d/%Y %H:%M:%S').replace(tzinfo=_tz(_td(hours=8))).timestamp()
        except Exception:
            continue
        rc = None
        for offset in (0, -1, 1, -2, 2):
            candidates = by_epoch.get(int(epoch) + offset)
            if candidates:
                rc = candidates[0]
                break
        if rc:
            if hasattr(m, 'reasoning_content'):
                m.reasoning_content = rc
            elif isinstance(m, dict):
                m['reasoning_content'] = rc


async def _get_active_date(session_id: str) -> str:
    """获取话题活跃日期（北京时间）"""
    from summary import topic_state_machine
    from time_utils import to_beijing_time
    status = await topic_state_machine.get_topic_status(session_id)
    start_time = status.get("start_time", "") if status else ""
    if start_time:
        return to_beijing_time(start_time)[:10]  # YYYY-MM-DD
    return ""




# 单次响应允许携带的最大消息数。上限存在的意义是：任何调用方（包括将来新增的
# 代码）都不能再无意中重新引入"一次拉全量"的路径——主会话曾因此涨到 60MB。
DEFAULT_MESSAGE_WINDOW = 200
MAX_MESSAGE_WINDOW = 500


def _normalize_limit(limit, default: int = DEFAULT_MESSAGE_WINDOW, cap: int = MAX_MESSAGE_WINDOW) -> int:
    """把外部传入的 limit 收敛到 [1, cap]。

    必须集中收敛，因为：(1) 旧代码用 `if limit:` 做真值判断，limit=0 会静默落到
    全量分支；(2) SQLite 里 `LIMIT -1` 表示无限制，负值等于绕过上限。
    """
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, cap)


def _rows_to_frontend(rows: list) -> list:
    """SQLite 行 → 前端消息。单条转换失败只跳过该条，不影响整个会话。"""
    frontend_msgs = []
    from api_sources.message_sources import for_session
    receipts = {session: for_session(session) for session in {r.get("session_id", "") for r in rows} if session}
    for r in rows:
        try:
            receipt = receipts.get(r.get("session_id", ""), {}).get(r.get("message_id") or r.get("id"))
            frontend_msgs.append(convert_msg_to_frontend({**r, "modelSource": receipt}))
        except Exception as e:
            logger.warning(f"消息转换失败，已跳过 (id={r.get('message_id', '?')}): {e}")
    return frontend_msgs


async def _load_session_messages_from_sqlite(session_id: str, limit: int = None) -> list:
    """从 SQLite（权威源）读取会话消息并转为前端格式。
    limit 为 None 时取全量正序；否则取最近 limit 条（正序）。"""
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()
    if limit:
        rows = await asyncio.to_thread(chronicle.get_messages_by_session_id, session_id, limit, True)
    else:
        rows = await asyncio.to_thread(chronicle.get_messages_by_session_id, session_id, 100000, False)
    return _rows_to_frontend(rows)


async def _load_session_window(session_id: str, limit: int = None,
                               before_id: str = None, before_ts: str = None,
                               before_row_id: int = 0) -> dict:
    """读取一个消息窗口：尾窗，或 before_id 游标之前的窗口。

    limit 为 None 时走全量兼容路径。手机端是设备上安装的 JS bundle，升级会滞后于
    后端；若此时对"不带 limit"的旧请求也窗口化，旧 bundle 的「服务端集合即存活集」
    合并逻辑会把它内存里的全部历史删掉（数据没丢，但用户看到历史凭空消失）。
    因此缺省保持全量，并打 DEPRECATED 日志作为"旧客户端是否还在调用"的信号；
    日志清零后再翻默认值。

    返回 {messages, has_more, oldest_id, oldest_ts, cursor_stale}。
    全量路径 has_more=False —— 前端据此走严格白名单（即旧行为）。
    """
    from event_chronicle import get_global_chronicle
    chronicle = get_global_chronicle()

    if limit is None:
        logger.warning("[DEPRECATED] unbounded session fetch: session_id=%s", session_id)
        rows = await asyncio.to_thread(
            chronicle.get_messages_by_session_id, session_id, 100000, False)
        return {"messages": _rows_to_frontend(rows), "has_more": False,
                "oldest_id": "", "oldest_ts": "", "cursor_stale": False}

    rows, has_more, cursor_stale = await asyncio.to_thread(
        chronicle.get_messages_before,
        session_id, _normalize_limit(limit), before_id, before_ts, before_row_id)
    messages = _rows_to_frontend(rows)
    # 注意：convert_msg_to_frontend 返回的是 Pydantic 模型，不是 dict
    oldest = messages[0] if messages else None
    return {
        "messages": messages,
        "has_more": has_more,
        "oldest_id": (getattr(oldest, "id", "") or "") if oldest is not None else "",
        "oldest_ts": (getattr(oldest, "timestamp", "") or "") if oldest is not None else "",
        "oldest_row_id": (getattr(oldest, "rowOrder", 0) or 0) if oldest is not None else 0,
        "cursor_stale": cursor_stale,
    }


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

@router.get("", response_model=SessionListResponse)
async def get_all_sessions(limit: int = DEFAULT_MESSAGE_WINDOW):
    """获取所有会话。limit 参数限制每个会话返回的最近消息数。

    缺省 200：不传 limit 时若对每个会话都全量读取，代价会按会话数放大。
    每个会话都带上 hasMore，前端才不会把窗口误当成完整历史。
    """
    _t0 = _time.monotonic()
    print(f"[GET /api/sessions] >>> REQUEST limit={limit}")

    session_manager = get_global_session_manager()
    sessions = await session_manager.get_all_session_metadata()
    primary_session_id = await session_manager.get_primary_session_id()
    current_session_id = get_latest_session_id() or ""

    session_list = []
    for session in sessions:
        window = await _load_session_window(session.id, limit)
        session_list.append(FrontendSession(
            id=session.id,
            title=session.title or "",
            messages=window["messages"],
            createdAt=session.createdAt or "",
            updatedAt=session.updatedAt or "",
            locked=getattr(session, 'locked', False),
            isPrimary=session.id == primary_session_id,
            hasMore=window["has_more"],
            oldestId=window["oldest_id"],
            oldestTs=window["oldest_ts"],
            oldestRowId=window.get("oldest_row_id", 0),
        ))

    _elapsed = _time.monotonic() - _t0
    print(f"[GET /api/sessions] <<< RESPONSE {len(session_list)} sessions in {_elapsed:.3f}s")

    return SessionListResponse(sessions=session_list, current_session_id=current_session_id)


@router.get("/{session_id}", response_model=FrontendSession)
async def get_session(session_id: str, limit: int = None,
                      before_id: str = None, before_ts: str = None,
                      before_row_id: int = 0):
    """获取特定会话。元数据走 session_manager（JSON 缓存），消息从 SQLite（权威源）直读。

    带 limit 时只返回窗口（可用 before_id/before_ts 继续向前翻页）；不带 limit 时
    保持全量返回，以兼容尚未升级的客户端（见 _load_session_window 的说明）。
    """
    session_manager = get_global_session_manager()
    session = await session_manager.get_session_metadata(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    window = await _load_session_window(session_id, limit, before_id, before_ts, before_row_id)

    return FrontendSession(
        id=session.id,
        title=session.title or "",
        messages=window["messages"],
        createdAt=session.createdAt or "",
        updatedAt=session.updatedAt or "",
        locked=getattr(session, 'locked', False),
        isPrimary=session.id == await session_manager.get_primary_session_id(),
        hasMore=window["has_more"],
        oldestId=window["oldest_id"],
        oldestTs=window["oldest_ts"],
        oldestRowId=window.get("oldest_row_id", 0),
        cursorStale=window["cursor_stale"],
    )


@router.get("/{session_id}/messages/count")
async def count_session_messages(session_id: str):
    session_manager = get_global_session_manager()
    session = await session_manager.get_session_metadata(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    from event_chronicle import get_global_chronicle
    total = await asyncio.to_thread(
        get_global_chronicle().count_messages_by_session_id, session_id,
    )
    return {"total": total, "updatedAt": session.updatedAt or ""}


@router.get("/{session_id}/messages/search")
async def search_session_messages(session_id: str, q: str = Query(default="", max_length=100),
                                  limit: int = 50, before_ts: str = "", before_row_id: int = 0,
                                  message_type: MessageSearchType = 'all'):
    q = q.strip()
    if not q and message_type == 'all':
        raise HTTPException(status_code=400, detail="请输入关键词或选择消息类型")
    if not await get_global_session_manager().get_session_metadata(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    from event_chronicle import get_global_chronicle
    return await asyncio.to_thread(
        get_global_chronicle().search_session_messages,
        session_id, q, limit, before_ts, before_row_id, message_type,
    )


@router.get("/{session_id}/messages/around/{message_id}")
async def get_session_messages_around(session_id: str, message_id: str):
    if not await get_global_session_manager().get_session_metadata(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    from event_chronicle import get_global_chronicle
    window = await asyncio.to_thread(
        get_global_chronicle().get_messages_around, session_id, message_id,
    )
    if window is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {
        "messages": _rows_to_frontend(window["messages"]),
        "hasMoreBefore": window["has_more_before"],
        "hasMoreAfter": window["has_more_after"],
    }


@router.get("/{session_id}/messages/after")
async def get_session_messages_after(session_id: str, after_id: str, after_ts: str = "",
                                     limit: int = DEFAULT_MESSAGE_WINDOW, after_row_id: int = 0):
    if not await get_global_session_manager().get_session_metadata(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    from event_chronicle import get_global_chronicle
    rows, has_more, cursor_stale = await asyncio.to_thread(
        get_global_chronicle().get_messages_after,
        session_id, after_id, after_ts, _normalize_limit(limit), after_row_id,
    )
    messages = _rows_to_frontend(rows)
    newest = messages[-1] if messages else None
    return {
        "messages": messages,
        "hasMore": has_more,
        "cursorStale": cursor_stale,
        "newestId": getattr(newest, "id", "") if newest else "",
        "newestTs": getattr(newest, "timestamp", "") if newest else "",
        "newestRowId": getattr(newest, "rowOrder", 0) if newest else 0,
    }


@router.post("", response_model=FrontendSession)
async def create_session(request: SessionCreateRequest):
    """创建新会话"""
    session_manager = get_global_session_manager()
    session = await session_manager.create_session(
        session_id=request.id,
        title=request.title or "新对话",
        messages=request.messages or [],
    )
    if not session:
        raise HTTPException(status_code=500, detail="创建会话失败")

    frontend_msgs = [convert_msg_to_frontend(m.model_dump() if hasattr(m, 'model_dump') else m)
                     for m in (session.messages or [])]
    return FrontendSession(
        id=session.id,
        title=session.title or "",
        messages=frontend_msgs,
        createdAt=session.createdAt or "",
        updatedAt=session.updatedAt or "",
        locked=False,
        isPrimary=session.id == await session_manager.get_primary_session_id(),
    )


@router.put("/{session_id}", response_model=FrontendSession)
async def update_session(session_id: str, request: SessionUpdateRequest):
    """更新会话元数据。消息由专用聊天/删除接口及 SQLite 持久化。"""
    session_manager = get_global_session_manager()
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    updates = {}
    if request.title is not None:
        updates["title"] = request.title
    if request.messages is not None:
        # Older mobile bundles used to send their entire rendered timeline
        # when changing a title or refreshing. That snapshot can be older
        # than a completed revoke; accepting it resurrects deleted JSON rows.
        logger.info("Ignoring legacy session message snapshot on metadata PUT")

    if updates:
        updated = await session_manager.update_session(session_id, updates)
        if not updated:
            raise HTTPException(status_code=500, detail="更新会话失败")


    session = await session_manager.get_session(session_id)
    # 元数据 PUT 的响应体没有调用方读取 messages（前端用本地乐观数据）；
    # 这里只返回一个窗口，避免"改个标题也回传 60MB"。
    frontend_msgs = await _load_session_messages_from_sqlite(session_id, DEFAULT_MESSAGE_WINDOW)
    return FrontendSession(
        id=session.id,
        title=session.title or "",
        messages=frontend_msgs,
        createdAt=session.createdAt or "",
        updatedAt=session.updatedAt or "",
        locked=getattr(session, 'locked', False),
    )


@router.post("/{session_id}/recover-thinking")
async def recover_thinking_from_conversation(session_id: str):
    """从 conversation_messages 恢复缺失的思考链"""
    session_manager = get_global_session_manager()
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        msgs = chronicle.get_messages_by_session_id(session_id, limit=500)
        rc_by_id = {}
        for m in msgs:
            mid = m.get("message_id", "") or m.get("id", "")
            rc = m.get("reasoning_content", "")
            if mid and rc:
                rc_by_id[mid] = rc

        updated = False
        for msg in session.messages:
            mid = getattr(msg, 'id', '') or (msg.get('id', '') if isinstance(msg, dict) else '')
            if mid in rc_by_id:
                rc = rc_by_id[mid]
                if hasattr(msg, 'reasoning_content'):
                    if not msg.reasoning_content:
                        msg.reasoning_content = rc
                        updated = True
                elif isinstance(msg, dict):
                    if not msg.get('reasoning_content'):
                        msg['reasoning_content'] = rc
                        updated = True

        if updated:
            all_msgs = [m.model_dump() if hasattr(m, 'model_dump') else m for m in session.messages]
            await session_manager.update_session(session_id, {"messages": all_msgs})

        return {"success": True, "recovered_count": len(rc_by_id), "updated": updated}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    session_manager = get_global_session_manager()
    if session_id == await session_manager.get_primary_session_id():
        raise HTTPException(status_code=403, detail="固定主会话不可删除")
    success = await session_manager.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "已删除", "session_id": session_id}


@router.post("/current/{session_id}")
async def set_current_session(session_id: str):
    """设置当前活跃会话"""
    session_manager = get_global_session_manager()
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    set_latest_session_id(session_id)
    try:
        import sys
        main_module = (sys.modules.get("main") or sys.modules.get("__main__"))
        if main_module.TASK_SCHEDULER_AVAILABLE:
            from task_scheduler.manager import get_global_task_manager
            get_global_task_manager().set_current_session_id(session_id)
    except Exception:
        pass
    return {"success": True, "current_session_id": session_id}


@router.get("/current/id")
async def get_current_session_id():
    """获取当前会话ID"""
    return {"session_id": get_latest_session_id() or ""}


@router.post("/clear")
async def clear_all_sessions(request: ClearSessionsRequest):
    """清除所有会话"""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="需要确认操作")
    session_manager = get_global_session_manager()
    sessions = await session_manager.get_all_sessions()
    for s in (sessions or []):
        await session_manager.delete_session(s.id)
    return {"message": "可删除会话已清除，固定主会话保留"}


@router.post("/{session_id}/lock")
async def toggle_session_lock(session_id: str):
    """切换会话锁定状态"""
    session_manager = get_global_session_manager()
    if session_id == await session_manager.get_primary_session_id():
        raise HTTPException(status_code=409, detail="固定主会话无需切换锁定")
    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    new_locked = not getattr(session, 'locked', False)
    updated = await session_manager.update_session(session_id, {"locked": new_locked})
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")
    return {"session_id": session_id, "locked": new_locked}
