"""Accept an edited turn with one atomic conversation replacement.

Receipts contain identities and cleanup metadata, never another copy of chat text.
Generation continues through the existing HTTP/WS turn guard after acceptance.
"""
import asyncio
import hashlib
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field
from event_chronicle import _pending_attachment_ids
from message_mutation_effects import _is_persistent_ui_notification, _cancel_pending_attachments, _affect_invalidate_sources, _reconcile_memory_v2_sources, _delete_memory_recall_traces, _delete_message_attachments, _message_images, _restore_main_chat_feed_announcements, _rebuild_scene_days
from shared_state import get_session_op_lock
from message_resend_payload import ATTACHMENT_COLUMNS, message_from_row, replacement_attachments, validate_message_files
from time_utils import now_iso, now_date, parse_ts, to_beijing_time
logger = logging.getLogger(__name__)

class ResendMessageRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    user_message_id: str = Field(min_length=1, max_length=200)
    replacement_message_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)

def replace_turn(chronicle, request: ResendMessageRequest, active_date: str, image_root=None) -> dict:
    """Rollback both deletion and insertion if either cannot commit."""
    content = request.content.strip()
    if not content or request.user_message_id == request.replacement_message_id:
        raise ValueError('重发内容或消息编号无效')
    fingerprint = hashlib.sha256(content.encode('utf-8')).hexdigest()
    with chronicle._lock, closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS conversation_resends (\n            replacement_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,\n            source_id TEXT NOT NULL, content_hash TEXT NOT NULL,\n            receipt_json TEXT NOT NULL, reconciled INTEGER NOT NULL DEFAULT 0,\n            UNIQUE(session_id, source_id))')
        previous = conn.execute('SELECT * FROM conversation_resends WHERE replacement_id=? OR (session_id=? AND source_id=?)', (request.replacement_message_id, request.session_id, request.user_message_id)).fetchone()
        if previous:
            if previous['session_id'] != request.session_id or previous['source_id'] != request.user_message_id or previous['content_hash'] != fingerprint:
                raise ValueError('这条消息已被修改，请保留当前编辑稿并核对聊天记录')
            saved = conn.execute('SELECT * FROM conversation_messages WHERE session_id=? AND message_id=? AND role=?', (request.session_id, previous['replacement_id'], 'user')).fetchone()
            if not saved or saved['content'] != content:
                raise ValueError('重发后的消息已被修改或撤回，请核对聊天记录')
            return {**json.loads(previous['receipt_json']), 'reconciled': bool(previous['reconciled']), 'message': message_from_row(saved)}
        target = conn.execute('SELECT * FROM conversation_messages WHERE session_id=? AND message_id=?', (request.session_id, request.user_message_id)).fetchone()
        if not target or target['role'] != 'user':
            raise ValueError('原消息已变化，重发未提交，编辑稿已保留')
        attachments = replacement_attachments(target, content)
        if image_root is not None:
            validate_message_files(message_from_row(target), image_root)
        rows = conn.execute('SELECT id,message_id,role,active_date,event_type,tool_calls,images FROM conversation_messages WHERE session_id=? AND (timestamp>? OR (timestamp=? AND id>=?)) ORDER BY timestamp,id', (request.session_id, target['timestamp'], target['timestamp'], target['id'])).fetchall()
        removed = [row for row in rows if not _is_persistent_ui_notification(dict(row))]
        conn.executemany('DELETE FROM conversation_messages WHERE id=?', [(row['id'],) for row in removed])
        stamp = now_iso()
        if not conn.execute("SELECT 1 FROM conversation_messages WHERE session_id=? AND active_date=? AND role IN ('user','assistant') LIMIT 1", (request.session_id, active_date)).fetchone():
            active_date = now_date()
        conn.execute('INSERT INTO conversation_messages (session_id,message_id,role,content,timestamp,active_date,calendar_date,images,voice_attachment,long_text_card,tool_calls,tool_summary,event_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (request.session_id, request.replacement_message_id, 'user', content, stamp, active_date, now_date(), *(attachments[column] for column in ATTACHMENT_COLUMNS)))
        message = message_from_row(dict(message_id=request.replacement_message_id, content=content, timestamp=stamp, **attachments))
        receipt = {'deleted_message_ids': [row['message_id'] for row in removed if row['message_id']], 'affected_event_types': sorted({row['event_type'] for row in removed if row['role'] == 'event' and row['event_type']}), 'active_dates': sorted({row['active_date'] for row in removed if row['active_date']} | {active_date}), 'pending_attachments': _pending_attachment_ids([row for row in removed if row['id'] != target['id']])}
        images = []
        for row in removed:
            try:
                images.extend(_message_images({'images': json.loads(row['images'] or '[]'), 'toolCalls': json.loads(row['tool_calls'] or '[]')}))
            except (ValueError, TypeError):
                pass
        from wander_manager.chat_attachment_boundary import sanitize_chat_image_metadata
        retained_urls = {metadata['url'] for image in _message_images(message) if (metadata := sanitize_chat_image_metadata(image))}
        receipt['_images'] = [{'url': metadata['url']} for image in images if (metadata := sanitize_chat_image_metadata(image)) and metadata['url'] not in retained_urls]
        conn.execute('INSERT INTO conversation_resends (replacement_id,session_id,source_id,content_hash,receipt_json) VALUES (?,?,?,?,?)', (request.replacement_message_id, request.session_id, request.user_message_id, fingerprint, json.dumps(receipt)))
        return {**receipt, 'reconciled': False, 'message': message}

def mark_reconciled(chronicle, message_id: str):
    with chronicle._lock, closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn, conn:
        conn.execute('UPDATE conversation_resends SET reconciled=1 WHERE replacement_id=?', (message_id,))

async def accept_resend(request: ResendMessageRequest):
    from chat_turn_guard import get_chat_turn_guard
    guard = get_chat_turn_guard()
    async with get_session_op_lock(request.session_id):
        if not guard.begin_mutation(request.session_id):
            raise HTTPException(409, '当前回复仍在进行，请结束后重发；编辑稿已保留')
        try:
            task = asyncio.create_task(_accept_locked(request))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        finally:
            guard.end_mutation(request.session_id)

async def _accept_locked(request: ResendMessageRequest):
    from event_chronicle import get_global_chronicle
    from session_manager import get_global_session_manager
    from summary import topic_state_machine
    manager = get_global_session_manager()
    if not manager or not await manager.get_session_metadata(request.session_id):
        raise HTTPException(404, '会话不存在，编辑稿已保留')
    chronicle = get_global_chronicle()
    await topic_state_machine.maybe_end_for_day_change(request.session_id)
    status = await topic_state_machine.get_topic_status(request.session_id) or {}
    active_date = now_date()
    if status.get('is_active') and status.get('start_time'):
        day = to_beijing_time(status['start_time'])[:10]
        now = parse_ts(now_iso())
        yesterday = (now - timedelta(days=1)).date().isoformat()
        if day == active_date or (day == yesterday and now.hour < topic_state_machine.DAY_CHANGE_HOUR):
            active_date = day
    try:
        runtime = sys.modules.get('main') or sys.modules.get('__main__')
        image_root = getattr(runtime, 'CHAT_IMAGE_DIR', Path(__file__).parent / 'static' / 'chat_images')
        result = await asyncio.to_thread(replace_turn, chronicle, request, active_date, image_root)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception('重发事务未确认')
        raise HTTPException(503, '重发结果暂未确认，请保留编辑稿并重试确认') from exc
    ids = result['deleted_message_ids']
    try:
        await manager.replace_messages(request.session_id, ids, result['message'])
    except Exception as exc:
        raise HTTPException(503, '新消息已保存，缓存校准未完成，请重试确认') from exc
    if not result['reconciled']:
        _cancel_pending_attachments(result['pending_attachments'])
        await asyncio.to_thread(_affect_invalidate_sources, ids, reason='revoke')
        await asyncio.to_thread(_reconcile_memory_v2_sources, ids, mutation='revoke')
        await asyncio.to_thread(_delete_memory_recall_traces, ids)
        await asyncio.to_thread(_delete_message_attachments, result.get('_images', []))
        await asyncio.to_thread(_rebuild_scene_days, result['active_dates'])
        await _restore_main_chat_feed_announcements(request.user_message_id)
        runtime = sys.modules.get('main') or sys.modules.get('__main__')
        wander = getattr(runtime, 'wander_manager', None)
        if wander:
            wander.retract_away_delivery()
        await topic_state_machine.recalculate_topic_status(request.session_id)
        from affect import get_affect_service
        speech = result['message']['content']
        if speech:
            await asyncio.to_thread(get_affect_service().observe_user_message, content=speech, source_id=result['message']['id'], session_id=request.session_id, occurred_at=result['message']['timestamp'])
        await asyncio.to_thread(mark_reconciled, chronicle, result['message']['id'])
    return {'accepted': True, 'message': result['message'], 'deleted_message_ids': result['deleted_message_ids'], 'affected_event_types': result['affected_event_types']}
