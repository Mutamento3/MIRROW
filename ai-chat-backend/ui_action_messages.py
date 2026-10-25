"""A deliberate UI invitation, not typed prose and not a chat-model request."""
import uuid
_dependencies = None

def configure_invitation(manager, persist, sessions, connections):
    global _dependencies
    _dependencies = (manager, persist, sessions, connections)

async def invite_action(content, event_type, action):
    """Shared durable button-message boundary; called only by local domain code."""
    if _dependencies is None:
        raise RuntimeError('快捷邀请消息通道尚未就绪')
    manager, persist, sessions, connections = _dependencies
    sm = sessions()
    sid = await sm.get_current_session_id()
    if not sid or not await sm.get_session(sid):
        raise RuntimeError('AI 的主会话尚未就绪')
    mid = 'ui_action_' + uuid.uuid4().hex
    await manager.add_message(sid, 'user', content, message_id=mid, event_type=event_type)
    from event_chronicle import get_global_chronicle
    row = get_global_chronicle().get_message_by_msg_id(mid)
    if not row or row.get('session_id') != sid or row.get('role') != 'user' or row.get('event_type') != event_type:
        raise RuntimeError('快捷邀请持久化未确认，未发起活动')
    timestamp = row.get('timestamp')
    if not isinstance(timestamp, str) or not timestamp:
        raise RuntimeError('邀请时间戳未确认，未发起活动')
    await persist(sid, mid, content, role='user', event_type=event_type)
    session = await sm.get_session(sid)
    if not session or not any(((m.get('id') if isinstance(m, dict) else m.id) == mid for m in session.messages)):
        raise RuntimeError('快捷邀请未确认同步，未发起活动')
    payload = {'type': 'quick_action_message', 'session_id': sid, 'message_id': mid, 'role': 'user', 'message': content, 'timestamp': timestamp, 'eventType': event_type, 'source': 'ui_action', 'action': action}
    for ws in list(connections()):
        try:
            await ws.send_json(payload)
        except Exception:
            pass
    return {'session_id': sid, 'source': 'ui_action', 'action': action, 'invitation_message_id': mid}
