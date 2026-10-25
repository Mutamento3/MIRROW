"""Shared side effects after authoritative conversation mutations."""
import logging
logger = logging.getLogger(__name__)

def _cancel_pending_attachments(pending: dict | None) -> None:
    """Stop background media work after its authoritative source was removed."""
    if not pending:
        return
    try:
        import sys
        main_module = sys.modules.get('main') or sys.modules.get('__main__')
        scheduler = getattr(main_module, 'behavior_scheduler', None)
        tools = getattr(scheduler, 'tools', {}) or {}
        voice_tool = tools.get('send_voice')
        if voice_tool:
            voice_tool.cancel_voices(list(pending.get('voice') or []))
    except Exception:
        logger.warning('异步附件取消待补偿；SQL 回写栅栏仍会阻止复活', exc_info=True)

def _affect_invalidate_sources(source_ids: list[str], *, reason: str) -> None:
    """Synchronously remove ghost Affect influence after SQLite mutation."""
    ids = [str(item) for item in source_ids or [] if str(item)]
    if not ids:
        return
    try:
        from affect import get_affect_service
        get_affect_service().invalidate_sources(ids, reason=reason)
    except Exception as exc:
        logger.warning('Affect source reconciliation pending (%s): %s', reason, type(exc).__name__)

def _delete_memory_recall_traces(message_ids: list[str]) -> int:
    """Delete derived per-message diagnostics without creating a new DB."""
    ids = [str(item) for item in message_ids or [] if str(item)]
    if not ids:
        return 0
    try:
        from memory_v2.trace_store import default_trace_db_path, get_default_trace_store
        if not default_trace_db_path().is_file():
            return 0
        return get_default_trace_store().delete_by_message_ids(ids)
    except Exception as exc:
        logger.warning('Memory V2 trace cleanup pending: %s', type(exc).__name__)
        return 0

def _reconcile_memory_v2_sources(source_ids: list[str], *, mutation: str, revision_material: str='') -> dict[str, object]:
    """Keep event truth and materialized recall aligned with message authority."""
    ids = list(dict.fromkeys((str(item) for item in source_ids or [] if str(item))))
    if not ids:
        return {'status': 'no_sources', 'source_count': 0, 'invalidated_event_count': 0, 'pending_active_dates': [], 'refresh_status': 'not_requested'}
    try:
        from memory_v2.source_lifecycle import reconcile_memory_v2_source_mutation
        result = reconcile_memory_v2_source_mutation(ids, mutation=mutation, revision_material=revision_material)
        if result.get('status') in {'error', 'reconciled_refresh_error'}:
            logger.warning('Memory V2 source reconciliation quarantined (%s): %s', mutation, result.get('error_type') or 'unknown')
        return result
    except Exception as exc:
        try:
            from memory_v2.context_shadow import quarantine_context_shadow_sources
            quarantine_context_shadow_sources(ids)
        except Exception:
            pass
        logger.warning('Memory V2 source reconciliation quarantined (%s): %s', mutation, type(exc).__name__)
        return {'status': 'error', 'source_count': len(ids), 'invalidated_event_count': 0, 'pending_active_dates': [], 'refresh_status': 'quarantined', 'error_type': type(exc).__name__}

def _message_images(message) -> list:
    """Read image metadata owned by a message and its XHS handoff."""
    values = []
    try:
        value = getattr(message, 'images', None)
        if value is None and isinstance(message, dict):
            value = message.get('images')
        if isinstance(value, list):
            values.extend(value)
        tool_calls = getattr(message, 'toolCalls', None)
        if tool_calls is None and isinstance(message, dict):
            tool_calls = message.get('toolCalls') or message.get('tool_calls')
        for tool_call in tool_calls or []:
            extra = getattr(tool_call, 'extra_data', None)
            if extra is None and isinstance(tool_call, dict):
                extra = tool_call.get('extra_data')
            if not isinstance(extra, dict) or extra.get('xhs_comment') is not True:
                continue
            images = extra.get('images')
            if isinstance(images, list):
                values.extend(images)
    except Exception:
        return values
    deduped = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        key = str(item.get('url') or item.get('id') or '')
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(item)
    return deduped

def _delete_message_attachments(images: list) -> None:
    """Use the static chat-image owner after the message is deleted."""
    if not images:
        return
    try:
        import sys
        main_module = sys.modules.get('main') or sys.modules.get('__main__')
        root = getattr(main_module, 'CHAT_IMAGE_DIR', '')
        if not root:
            return
        from wander_manager.chat_attachment_boundary import delete_owned_chat_attachments
        delete_owned_chat_attachments(images, root)
    except Exception:
        logger.warning('chat attachment cleanup unavailable')

def _is_persistent_ui_notification(message) -> bool:
    """Keep independently occurred UI events when surrounding chat is removed.

    These notifications record a domain mutation, a delivered physical action,
    or a completed observation.  Revoking a chat turn does not undo that event.
    Context-only notices without an independent event type remain revoke-owned.
    """
    if isinstance(message, dict):
        role = message.get('role', '')
        event_type = message.get('eventType') or message.get('event_type') or ''
    else:
        role = getattr(message, 'role', '')
        event_type = getattr(message, 'eventType', '') or getattr(message, 'event_type', '')
    durable_events = {'wish_board_update', 'social_feed_update', 'band_action', 'checkup_observation', 'ambient_listening_observation'}
    return str(role or '') == 'notification' and str(event_type or '') in durable_events

async def _restore_main_chat_feed_announcements(message_id: str) -> int:
    """Restore only feed notices claimed by this main-chat turn."""
    try:
        from social_feed import get_social_feed_store
        return await get_social_feed_store().restore_main_chat_announcements(message_id)
    except Exception as exc:
        logger.warning('restore main-chat social notices failed: %s', type(exc).__name__)
        return 0

def _rebuild_scene_days(active_dates):
    """Repair derived scene rows after a destructive/edit operation."""
    if not active_dates:
        return
    try:
        from scene_manager.scene import rebuild_day
        for active_date in sorted({str(d)[:10] for d in active_dates if d}):
            rebuild_day(active_date)
    except Exception as exc:
        logger.warning('Scene rebuild failed after message mutation: %s', exc)
