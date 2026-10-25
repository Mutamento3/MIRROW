"""Small, idempotent delivery path for durable UI notifications.

Notifications are independent ``role=event`` rows in the existing
conversation authority.  They deliberately do not call the wander
push path, topic state machine, phone push, or a second history writer.  A
separate read-time allow-list projects concrete persisted notices into context
and Memory V2 without changing their notification role or delivery contract.
The injected callbacks keep this module independent from ``main.py`` and make
the delivery boundary easy to test with temporary/fake stores.
"""
from __future__ import annotations
import logging
import json
from datetime import datetime
from typing import Any, Callable, Iterable, Optional
from message_roles import EVENT_ROLE
from time_utils import now_iso
logger = logging.getLogger(__name__)
NOTIFICATION_CONTENT = {'wish_board_update': '🌠 许愿板有了新动静 · 点击查看', 'social_feed_update': '💌 共域有了新动静 · 点击查看', 'checkup_observation': '👀 AI刚刚查了个岗'}
_SOCIAL_FEED_ACTION_CONTENT = {'post': '💌 AI 发了一条共域动态', 'comment': '💬 AI 评论了一条共域动态', 'like': '❤️ AI 给一条共域动态点了赞', 'delete': '🗑️ AI 删除了一条自己的共域动态', 'set_visibility': '🔒 AI 调整了一条共域动态的可见范围'}

def social_feed_action_content(action: str) -> str:
    """Describe a confirmed feed mutation without inventing its target content."""
    return _SOCIAL_FEED_ACTION_CONTENT.get(str(action or ''), '💌 AI 更新了共域')

def wish_board_notification_details(results: list[dict] | None, metadata: Optional[dict[str, Any]]=None) -> Optional[tuple[str, dict[str, Any]]]:
    """Turn committed wish mutations into one UI-only message and routing facts."""
    mutations = [item for item in results or [] if isinstance(item, dict) and item.get('mutation') is True]
    if not mutations:
        return None
    changes: list[dict[str, Any]] = []
    for item in mutations:
        payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
        event_type = str(item.get('event_type') or item.get('eventType') or '')
        outcome = str(item.get('outcome') or '')
        title = str(item.get('title') or payload.get('title') or '').strip()[:120]
        try:
            wish_id = int(item.get('wish_id') or payload.get('wish_id'))
        except (TypeError, ValueError):
            wish_id = None
        if event_type == 'reaffirmed' or outcome == 'reaffirmed':
            change_kind = 'reaffirm'
            before = item.get('previous_count', payload.get('from_count'))
            after = item.get('new_count', payload.get('to_count'))
            text = f'🌠 AI 又许了一次「{title}」 · {before}→{after}'
        elif event_type == 'created' or outcome == 'created':
            change_kind = 'create'
            before, after = (0, 1)
            text = f'🌠 AI 新许了「{title}」'
        elif event_type == 'comment' or outcome == 'commented':
            change_kind = 'comment'
            before = after = None
            text = f'💬 AI 回复了「{title}」'
        elif event_type in {'retained', 'deleted'} or outcome in {'impossible_kept', 'deleted'}:
            change_kind = 'status'
            before = item.get('from_status', payload.get('from'))
            after = item.get('to_status', payload.get('to')) or ('impossible_kept' if event_type == 'retained' else 'deleted')
            verb = '保留了天方夜谭愿望' if after == 'impossible_kept' else '删除了天方夜谭愿望'
            text = f'🌠 AI {verb}「{title}」'
        else:
            change_kind = 'update'
            before = item.get('before')
            after = item.get('after')
            text = f'🌠 许愿板更新了「{title}」'
        changes.append({'target_wish_id': wish_id, 'change_kind': change_kind, 'before': before, 'after': after, 'title': title, 'content': text})
    primary = changes[0]
    content = '；'.join((change['content'] for change in changes))
    return (content, {**(metadata or {}), 'target_wish_id': primary['target_wish_id'], 'change_kind': primary['change_kind'], 'before': primary['before'], 'after': primary['after'], 'title': primary['title'], 'changes': changes})

class NotificationService:
    """Persist and broadcast one generic UI notification.

    ``conversation_manager`` is the authoritative SQLite-backed message
    manager.  ``persist_special`` mirrors the message into the session JSON
    cache used by the frontend.  Both are injected to avoid importing the
    application module from runtime code.
    """

    def __init__(self, *, conversation_manager: Any, persist_special: Optional[Callable[..., Any]]=None, session_manager: Any=None, connections: Optional[Iterable[Any] | Callable[[], Iterable[Any]]]=None, chronicle: Any=None):
        self.conversation_manager = conversation_manager
        self.persist_special = persist_special
        self.session_manager = session_manager
        self.connections = connections
        self.chronicle = chronicle

    def _connections(self) -> list[Any]:
        value = self.connections() if callable(self.connections) else self.connections
        return list(value or [])

    @staticmethod
    def message_id(event_type: str, source_id: str) -> str:
        return f'notification:{event_type}:{source_id}'

    def _already_persisted(self, message_id: str) -> bool:
        chronicle = self.chronicle
        if chronicle is None:
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
            except Exception:
                return False
        try:
            return chronicle.get_message_by_msg_id(message_id) is not None
        except Exception:
            return False

    async def emit(self, *, session_id: str, event_type: str, source_id: str, content: Optional[str]=None, metadata: Optional[dict[str, Any]]=None) -> dict[str, Any]:
        """Persist then broadcast a notification, once per source mutation."""
        if not session_id or not source_id:
            return {'mutated': False, 'reason': 'missing_identity'}
        event_type = str(event_type or 'notification').strip() or 'notification'
        message_id = self.message_id(event_type, str(source_id))
        metadata = dict(metadata or {})
        if event_type == 'checkup_observation':
            items = list(metadata.get('items') or [])
            from checkup_assessment import source_status
            metadata.setdefault('source_status', source_status(items))
            metadata.setdefault('goal_assessment', {'status': 'not_assessed'})
            succeeded = sum((1 for item in items if item.get('status') == 'success'))
            unclear = sum((1 for item in items if item.get('status') in {'partial', 'failure', 'skipped'}))
            suffix = f' · {succeeded}项成功'
            if unclear:
                suffix += f'，{unclear}项没看清'
            conclusion = {'confirmed': '来源齐备', 'contradicted': '来源存在差异', 'uncertain': '来源不完整'}.get(str(metadata.get('conclusion') or 'uncertain'), '来源状态未知')
            text = content or f'👀 AI刚刚查了个岗{suffix} · {conclusion}'
        else:
            text = content or NOTIFICATION_CONTENT.get(event_type) or '有新的动态 · 点击查看'
        timestamp = now_iso()
        active_date = timestamp[:10]
        if self._already_persisted(message_id):
            return {'mutated': False, 'message_id': message_id, 'reason': 'already_persisted'}
        tool_calls = json.dumps(metadata.get('items') or [], ensure_ascii=False) if metadata else None
        persisted = await self.conversation_manager.add_message(session_id, EVENT_ROLE, text, message_id=message_id, active_date=active_date, calendar_date=timestamp[:10], event_type=event_type, tool_calls=tool_calls, timestamp_override=timestamp, require_persistence=True)
        if isinstance(persisted, dict) and persisted.get('timestamp'):
            timestamp = str(persisted['timestamp'])
        if self.persist_special is not None:
            await self.persist_special(session_id=session_id, msg_id=message_id, content=text, role=EVENT_ROLE, event_type=event_type, tool_calls=metadata.get('items') or None, metadata=metadata or None, timestamp=timestamp)
        if self.session_manager is not None:
            try:
                await self.session_manager.flush_session(session_id)
            except Exception:
                logger.debug('notification session flush failed', exc_info=True)
        payload = {'type': 'notification', 'role': EVENT_ROLE, 'eventType': event_type, 'event_type': event_type, 'message_id': message_id, 'msg_id': message_id, 'session_id': session_id, 'content': text, 'message': text, 'timestamp': timestamp, 'checkup': metadata if event_type == 'checkup_observation' else None, 'toolCalls': metadata.get('items') or [], 'metadata': metadata, 'notificationMetadata': metadata}
        stale = []
        for websocket in self._connections():
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        if stale and hasattr(self.connections, 'difference_update'):
            self.connections.difference_update(stale)
        return {'mutated': True, 'message_id': message_id, 'payload': payload}
__all__ = ['NotificationService', 'NOTIFICATION_CONTENT', 'social_feed_action_content']
