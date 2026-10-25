"""消息操作 API 路由 — 从 main.py 提取"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from session_manager import get_global_session_manager
from routers.shared_models import (
    RevokeMessageRequest, DeleteMessageRequest,
    ClearSessionMessagesRequest, EditMessageRequest,
    EditLongTextCardRequest,
)
from shared_state import get_session_op_lock

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/messages", tags=["messages"])

from message_resend import ResendMessageRequest, accept_resend


@router.post('/resend')
async def resend_message(request: ResendMessageRequest):
    return await accept_resend(request)


from message_mutation_effects import (
    _cancel_pending_attachments,
    _affect_invalidate_sources,
    _delete_memory_recall_traces,
    _reconcile_memory_v2_sources,
    _message_images,
    _delete_message_attachments,
    _is_persistent_ui_notification,
    _restore_main_chat_feed_announcements,
    _rebuild_scene_days,
)


# ═══════════════════════════════════════════════════════════
# 辅助函数 — _match_v2_message_ids（从 main.py 直接搬运，零依赖改动）
# ═══════════════════════════════════════════════════════════

def _match_v2_message_ids(message_ids_to_delete: list, all_messages: list, conv_messages: list) -> list:
    """三步匹配 V2/SQLite 侧对应的 message_id 列表。"""
    conv_id_set = {m.get("message_id", "") or m.get("id", "") for m in conv_messages if m.get("message_id") or m.get("id")}
    matched_ids = []
    unmatched_ids = []

    for msg_id in message_ids_to_delete:
        # Step 1: 精确 ID 匹配
        if msg_id in conv_id_set:
            matched_ids.append(msg_id)
            continue
        # Step 2: SQLite 直查
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            found = chronicle.get_message_by_msg_id(msg_id)
            if found:
                matched_ids.append(msg_id)
                continue
        except Exception:
            pass
        # Step 3: 内容前缀回退
        unmatched_ids.append(msg_id)

    if unmatched_ids:
        for msg_id in unmatched_ids:
            # 在 all_messages 中找匹配的 session 层消息
            target_content = ""
            target_role = ""
            for m in all_messages:
                mid = getattr(m, 'id', '') if hasattr(m, 'id') else m.get('id', '') if isinstance(m, dict) else ''
                if mid == msg_id:
                    c = getattr(m, 'content', '') if hasattr(m, 'content') else m.get('content', '') if isinstance(m, dict) else ''
                    target_content = (c or "")[:200].strip()
                    r = getattr(m, 'role', '') if hasattr(m, 'role') else m.get('role', '') if isinstance(m, dict) else ''
                    target_role = r
                    break
            if target_content:
                sc = target_content[:50]
                for cm in conv_messages:
                    cc = (cm.get("content", "") or "")[:50]
                    cr = cm.get("role", "")
                    if cc == sc and cr == target_role:
                        matched_ids.append(cm.get("message_id", cm.get("id", "")))
                        break

    return matched_ids


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

@router.post("/revoke")
async def revoke_message(request: RevokeMessageRequest):
    """SQL-first revoke of the user turn and every later conversation row."""
    import sys
    main_module = sys.modules.get("main") or sys.modules.get("__main__")

    _sop_lock = get_session_op_lock(request.session_id)
    async with _sop_lock:
        session_manager = get_global_session_manager()
        session = await session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        # The SQL timeline is authoritative even when the session display
        # cache is stale or lacks newly-written event rows.
        try:
            from event_chronicle import get_global_chronicle
            deleted = get_global_chronicle().delete_conversation_tail(
                request.session_id, request.user_message_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as e:
            logger.exception("撤回 SQL 事务失败")
            raise HTTPException(status_code=503, detail="数据库撤回失败，消息未变更") from e
        message_ids_to_delete = deleted["deleted_ids"]
        if not message_ids_to_delete:
            raise HTTPException(status_code=404, detail="未找到要撤回的消息")
        _cancel_pending_attachments(deleted.get("pending_attachments"))
        deleted_set = set(message_ids_to_delete)
        deleted_images = []
        remaining = []
        for message in session.messages:
            mid = getattr(message, 'id', '') if hasattr(message, 'id') else message.get('id', '') if isinstance(message, dict) else ''
            if mid in deleted_set:
                deleted_images.extend(_message_images(message))
            else:
                remaining.append(message)
        remaining_dicts = [m.model_dump() if hasattr(m, 'model_dump') else m for m in remaining]
        try:
            await session_manager.update_session(request.session_id, {"messages": remaining_dicts}, merge=False)
        except Exception:
            logger.warning("撤回已提交，session 显示缓存更新失败；下次 SQLite 重载会纠正", exc_info=True)

        _affect_invalidate_sources(message_ids_to_delete, reason="revoke")
        memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
            message_ids_to_delete, mutation="revoke"
        )
        _delete_message_attachments(deleted_images)
        deleted_memory_traces = _delete_memory_recall_traces(message_ids_to_delete)

        # 4. 撤回漫想投递
        try:
            wander_manager = main_module.wander_manager
            if wander_manager:
                wander_manager.retract_away_delivery()
        except Exception:
            pass

        # Recalculate only after the authoritative SQL transaction committed.
        try:
            await main_module.topic_state_machine.recalculate_topic_status(request.session_id)
        except Exception:
            pass

        # A reminder claimed by this exact main-chat user turn becomes unread
        # again when that turn is revoked.  UI/wander/tool reads have a
        # different source and are deliberately untouched.
        restored_feed_notifications = await _restore_main_chat_feed_announcements(
            request.user_message_id
        )
        return {
            "message": "消息撤回成功",
            "deleted_count": len(message_ids_to_delete),
            "deleted_message_ids": message_ids_to_delete,
            "affected_event_types": deleted["event_types"],
            "v2_deleted_count": len(message_ids_to_delete),
            "v2_unmatched_ids": [],
            "v2_fallback_used": False,
            "preserved_ui_message_ids": [],
            "restored_feed_notifications": restored_feed_notifications,
            "deleted_memory_traces": deleted_memory_traces,
            "memory_v2_source_lifecycle": memory_v2_source_lifecycle,
        }


@router.post("/delete")
async def delete_single_message(request: DeleteMessageRequest):
    """删除单条消息"""
    _sop_lock = get_session_op_lock(request.session_id)
    async with _sop_lock:
        session_manager = get_global_session_manager()
        session = await session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        deleted_images = []
        for message in session.messages:
            mid = getattr(message, 'id', '') if hasattr(message, 'id') else message.get('id', '') if isinstance(message, dict) else ''
            if mid == request.message_id:
                deleted_images.extend(_message_images(message))
        # SQLite is the only message authority.  Confirm the mutation before
        # touching the session display cache, so a failed DELETE cannot make
        # the row vanish and reappear on reload.
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            deleted = chronicle.delete_one_with_associated_events(
                request.session_id, request.message_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as e:
            logger.exception("SQLite 删除失败")
            raise HTTPException(status_code=503, detail="数据库删除失败，消息未变更") from e
        deleted_ids = deleted["deleted_ids"]
        if not deleted_ids:
            raise HTTPException(status_code=404, detail="未找到要删除的消息")
        _cancel_pending_attachments(deleted.get("pending_attachments"))
        deleted_set = set(deleted_ids)
        remaining_messages = [m for m in session.messages if
                              (getattr(m, 'id', '') if hasattr(m, 'id') else m.get('id', '') if isinstance(m, dict) else '') not in deleted_set]
        remaining_dicts = [m.model_dump() if hasattr(m, 'model_dump') else m for m in remaining_messages]
        try:
            await session_manager.update_session(request.session_id, {"messages": remaining_dicts}, merge=False)
        except Exception:
            logger.warning("删除已提交，session 显示缓存更新失败；下次 SQLite 重载会纠正", exc_info=True)

        _affect_invalidate_sources(deleted_ids, reason="delete")
        memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
            deleted_ids, mutation="delete"
        )

        _delete_message_attachments(deleted_images)
        deleted_memory_traces = _delete_memory_recall_traces(deleted_ids)

        # 撤回漫想投递
        try:
            import sys
            m = sys.modules.get("main") or sys.modules.get("__main__")
            wander_manager = m.wander_manager if m else None
            if wander_manager:
                wander_manager.retract_away_delivery()
        except Exception:
            pass

        try:
            import sys
            m = sys.modules.get("main") or sys.modules.get("__main__")
            if m:
                await m.topic_state_machine.recalculate_topic_status(request.session_id)
        except Exception:
            pass

        return {
            "message": "消息删除成功",
            "deleted_message_id": request.message_id,
            "deleted_message_ids": deleted_ids,
            "affected_event_types": deleted["event_types"],
            "v2_deleted": len(deleted_ids),
            "deleted_memory_traces": deleted_memory_traces,
            "memory_v2_source_lifecycle": memory_v2_source_lifecycle,
        }


@router.post("/clear")
async def clear_session_messages(request: ClearSessionMessagesRequest):
    """清空会话所有消息"""
    _sop_lock = get_session_op_lock(request.session_id)
    async with _sop_lock:
        session_manager = get_global_session_manager()
        session = await session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        deleted_images = []
        cleared_session_message_ids = []
        for message in session.messages:
            if not _is_persistent_ui_notification(message):
                deleted_images.extend(_message_images(message))
                message_id = (
                    getattr(message, "id", "")
                    if hasattr(message, "id")
                    else message.get("id", "") if isinstance(message, dict) else ""
                )
                if message_id:
                    cleared_session_message_ids.append(message_id)

        affected_scene_dates = set()
        cleared_source_ids = []
        preserved_ui_message_ids = []
        try:
            from event_chronicle import get_global_chronicle

            for message in get_global_chronicle().get_messages_by_session_id(request.session_id, 10000):
                message_id = message.get("message_id") or message.get("id")
                if _is_persistent_ui_notification(message):
                    if message_id:
                        preserved_ui_message_ids.append(message_id)
                elif message_id:
                    cleared_source_ids.append(message_id)
                if message.get("active_date"):
                    affected_scene_dates.add(message["active_date"])
        except Exception:
            pass

        preserved_session_messages = [
            m.model_dump() if hasattr(m, 'model_dump') else m
            for m in session.messages if _is_persistent_ui_notification(m)
        ]
        await session_manager.update_session(request.session_id, {"messages": preserved_session_messages}, merge=False)

        # 同步清理 SQLite
        sqlite_clear_ok = False
        memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
            [], mutation="clear"
        )
        try:
            from event_chronicle import get_global_chronicle
            get_global_chronicle().delete_conversation_messages_by_session(
                request.session_id, exclude_message_ids=preserved_ui_message_ids
            )
            sqlite_clear_ok = True
        except Exception as e:
            logger.warning(f"clear: SQLite 清理失败: {e}")

        if sqlite_clear_ok:
            _affect_invalidate_sources(cleared_source_ids, reason="clear")
            memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
                cleared_source_ids, mutation="clear"
            )

        _rebuild_scene_days(affected_scene_dates)
        _delete_message_attachments(deleted_images)
        deleted_memory_traces = _delete_memory_recall_traces(
            list(dict.fromkeys((*cleared_session_message_ids, *cleared_source_ids)))
        )

        try:
            import sys
            m = sys.modules.get("main") or sys.modules.get("__main__")
            if m:
                await m.topic_state_machine.recalculate_topic_status(request.session_id)
        except Exception:
            pass

        try:
            import sys
            m = sys.modules.get("main") or sys.modules.get("__main__")
            wander_manager = m.wander_manager if m else None
            if wander_manager:
                wander_manager.retract_away_delivery()
        except Exception:
            pass

    return {
        "message": "会话消息已清除", "session_id": request.session_id,
        "preserved_ui_message_ids": preserved_ui_message_ids,
        "deleted_memory_traces": deleted_memory_traces,
        "memory_v2_source_lifecycle": memory_v2_source_lifecycle,
    }


@router.post("/edit")
async def edit_message(request: EditMessageRequest):
    """编辑一条消息"""
    try:
        from affect.sidecar import parse_affect_sidecar
        clean_new_content = parse_affect_sidecar(request.new_content).clean_content
    except Exception:
        clean_new_content = request.new_content
    _sop_lock = get_session_op_lock(request.session_id)
    async with _sop_lock:
        session_manager = get_global_session_manager()
        session = await session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        affected_scene_dates = set()
        edit_role = "user"
        try:
            from event_chronicle import get_global_chronicle

            before_edit = get_global_chronicle().get_message_by_msg_id(request.message_id)
            if before_edit and before_edit.get("active_date"):
                affected_scene_dates.add(before_edit["active_date"])
            if before_edit and before_edit.get("role"):
                edit_role = str(before_edit.get("role"))
        except Exception:
            pass

        found = False
        updated_messages = []
        for m in session.messages:
            mid = getattr(m, 'id', '') if hasattr(m, 'id') else m.get('id', '') if isinstance(m, dict) else ''
            if mid == request.message_id:
                found = True
                if hasattr(m, 'model_dump'):
                    d = m.model_dump()
                    d['content'] = clean_new_content
                    updated_messages.append(d)
                elif isinstance(m, dict):
                    m['content'] = clean_new_content
                    updated_messages.append(m)
                else:
                    updated_messages.append(m)
            else:
                updated_messages.append(m.model_dump() if hasattr(m, 'model_dump') else m)

        if not found:
            raise HTTPException(status_code=404, detail="未找到要编辑的消息")

        await session_manager.update_session(request.session_id, {"messages": updated_messages}, merge=False)

        # 同步到 V2
        edit_ok = False
        memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
            [], mutation="edit"
        )
        try:
            from summary import conversation_manager
            edit_ok = await conversation_manager.edit_message(request.session_id, request.message_id, clean_new_content)
        except Exception:
            pass

        if edit_ok:
            _rebuild_scene_days(affected_scene_dates)
            memory_v2_source_lifecycle = _reconcile_memory_v2_sources(
                [request.message_id],
                mutation="edit",
                revision_material=clean_new_content,
            )
            try:
                from affect import get_affect_service
                get_affect_service().edit_source(
                    source_id=request.message_id, new_content=clean_new_content,
                    session_id=request.session_id, role=edit_role,
                )
            except Exception as exc:
                logger.warning("Affect source edit reconciliation pending: %s", type(exc).__name__)

        return {
            "message": "消息编辑成功", "message_id": request.message_id,
            "memory_v2_source_lifecycle": memory_v2_source_lifecycle,
        }


@router.put("/long-text-card")
async def edit_long_text_card(request: EditLongTextCardRequest):
    """无痕原地编辑一张用户长文卡片，正文仍从属于原消息。"""
    from long_text_cards import normalize_long_text_card
    import json
    try:
        card = normalize_long_text_card(request.long_text_card, create_id=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    async with get_session_op_lock(request.session_id):
        session_manager = get_global_session_manager()
        session = await session_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        updated = []
        found = False
        old_card = None
        for message in session.messages:
            data = message.model_dump() if hasattr(message, "model_dump") else dict(message)
            if data.get("id") == request.message_id:
                if data.get("role") != "user":
                    raise HTTPException(status_code=400, detail="只能编辑人类伙伴发送的长文卡片")
                old = data.get("longTextCard") or data.get("long_text_card")
                if not old:
                    raise HTTPException(status_code=404, detail="该消息没有长文卡片")
                if str(old.get("id") if isinstance(old, dict) else "") != card["id"]:
                    raise HTTPException(status_code=400, detail="卡片 id 不匹配")
                old_card = old
                data["longTextCard"] = card
                found = True
            updated.append(data)
        if not found:
            raise HTTPException(status_code=404, detail="未找到要编辑的长文卡片")
        try:
            from event_chronicle import get_global_chronicle
            if not get_global_chronicle().update_message_long_text_card(request.message_id, json.dumps(card, ensure_ascii=False)):
                raise HTTPException(status_code=409, detail="SQLite 中没有对应消息，未修改卡片")
            await session_manager.update_session(request.session_id, {"messages": updated}, merge=False)
            await session_manager.flush_session(request.session_id)
        except HTTPException:
            raise
        except Exception as exc:
            if old_card is not None:
                try:
                    get_global_chronicle().update_message_long_text_card(
                        request.message_id,
                        json.dumps(old_card, ensure_ascii=False),
                    )
                except Exception:
                    logger.error("长文卡片编辑回滚失败: %s", request.message_id)
            logger.warning("长文卡片编辑失败: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="长文卡片保存失败，未确认完成")
    return {"message": "长文卡片已保存", "message_id": request.message_id, "longTextCard": card}


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    feedback_type: str  # "like" or "dislike"
    reason: str = ""


@router.post("/feedback")
async def submit_feedback(request: FeedbackRequest):
    """提交赞/踩反馈"""
    if request.feedback_type not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="feedback_type 必须为 like 或 dislike")

    from event_chronicle import get_global_chronicle
    from datetime import datetime

    chronicle = get_global_chronicle()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    with chronicle._lock:
        import sqlite3
        with sqlite3.connect(chronicle.db_path, timeout=5) as conn:
            conn.execute(
                "INSERT INTO message_feedback (message_id, session_id, feedback_type, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (request.message_id, request.session_id, request.feedback_type, request.reason, now)
            )
            conn.commit()

    logger.info(f"反馈已保存: {request.feedback_type} on {request.message_id}")
    return {"ok": True, "feedback_type": request.feedback_type, "message_id": request.message_id}


