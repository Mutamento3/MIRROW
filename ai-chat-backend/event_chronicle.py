"""
事件纪 (Event Chronicle) 模块
管理重要事件（用户手动输入）和日记（自动生成）以及维护任务进度追踪
"""
import sqlite3
import json
import os
import glob
import hashlib
import threading
import logging
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple, Iterable
from dataclasses import dataclass, asdict, field
from time_utils import to_beijing_time, now_date
from truncation_config import get_truncation_limit
logger = logging.getLogger(__name__)
_MSG_COLS = 'id AS row_id, active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id'
_MSG_KEYS = ('row_id', 'active_date', 'calendar_date', 'role', 'content', 'timestamp', 'session_id', 'reasoning_content', 'message_id', 'is_wander', 'is_sentinel', 'is_reminder', 'tool_summary', 'tool_calls', 'event_type', 'images', 'voice_attachment', 'thinking_chain_json', 'long_text_card', 'event_parent_id')

def _split_new_tool_events(raw_calls, *, parent_id: str) -> tuple[Optional[str], list[dict]]:
    """Move eligible synchronous receipts from a new assistant row to events.

    Async media and independently owned domain cards stay on their existing
    attachment path until their own completion/update lifecycle is migrated.
    Invalid or historical receipts are left untouched, never guessed into a
    newly asserted occurrence.
    """
    if not parent_id or not raw_calls:
        return (raw_calls, [])
    try:
        calls = json.loads(raw_calls) if isinstance(raw_calls, str) else raw_calls
    except (TypeError, ValueError):
        return (raw_calls, [])
    if not isinstance(calls, list):
        return (raw_calls, [])
    from context_builder.tool_event_projection import project_tool_event
    retained = []
    events = []
    seen = set()
    for call in calls:
        if not isinstance(call, dict):
            retained.append(call)
            continue
        event_id = str(call.get('event_id') or '')
        occurred_at = str(call.get('completed_at') or '')
        name = str(call.get('tool') or call.get('name') or '')
        extra = call.get('extra_data') if isinstance(call.get('extra_data'), dict) else {}
        independently_owned = name in {'send_voice', 'band_touch', 'ambient_event_link'} or any((extra.get(key) for key in ('lounge_visit', 'music_card', 'xhs_comment', 'xhs_post_card'))) or extra.get('attachment_kind') == 'music'
        if not event_id.startswith('tool_event:') or not occurred_at or independently_owned or (not project_tool_event(call)):
            retained.append(call)
            continue
        if event_id not in seen:
            seen.add(event_id)
            events.append({'event_id': event_id, 'timestamp': occurred_at, 'tool_calls': json.dumps([call], ensure_ascii=False), 'parent_message_id': parent_id})
    if not events:
        return (raw_calls, [])
    return (json.dumps(retained, ensure_ascii=False) if retained else None, events)

def _pending_attachment_ids(rows) -> dict[str, list[str]]:
    ids: dict[str, set[str]] = {'image': set(), 'voice': set()}
    for row in rows:
        try:
            calls = json.loads(row['tool_calls'] or '[]')
        except (TypeError, ValueError):
            continue
        if not isinstance(calls, list):
            continue
        for call in calls:
            extra = call.get('extra_data') if isinstance(call, dict) else None
            if not isinstance(extra, dict) or extra.get('attachment_status') != 'pending':
                continue
            if call.get('tool') == 'send_voice' and extra.get('voice_id'):
                ids['voice'].add(str(extra['voice_id']))
    return {kind: sorted(values) for kind, values in ids.items()}
DATE_PRECISION_FULL = 'full'
DATE_PRECISION_YEAR_MONTH = 'year_month'
DATE_PRECISION_YEAR_ONLY = 'year_only'

@dataclass
class ImportantEvent:
    """重要事件（用户手动输入）"""
    id: str
    date_string: str
    date_precision: str
    event_text: str
    emotion: str
    created_at: str
    updated_at: str

@dataclass
class DiaryEntry:
    """日记（自动生成，每天一条）"""
    id: str
    date: str
    content: str
    summary_ids: List[str]
    generated_at: str
    structured_data: Dict[str, Any] = field(default_factory=dict)

    @property
    def structured_json(self) -> str:
        """Compatibility view for code that wants the persisted JSON text."""
        return json.dumps(self.structured_data or {}, ensure_ascii=False)

@dataclass
class MaintenanceTaskStatus:
    """单个维护任务的状态"""
    name: str
    status: str
    progress: int
    message: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None

@dataclass
class MaintenanceStatus:
    """整体维护进度"""
    tasks: List[MaintenanceTaskStatus]
    last_run: Optional[str] = None
    last_success: Optional[str] = None
    overall_progress: int = 0

    def get_task(self, name: str) -> Optional[MaintenanceTaskStatus]:
        for t in self.tasks:
            if t.name == name:
                return t
        return None
_maintenance_status = MaintenanceStatus(tasks=[MaintenanceTaskStatus(name='diary_generation', status='pending', progress=0, message='等待运行'), MaintenanceTaskStatus(name='bucket_migration', status='pending', progress=0, message='等待运行'), MaintenanceTaskStatus(name='log_cleanup', status='pending', progress=0, message='等待运行')])
_maintenance_lock = threading.Lock()

def get_maintenance_status() -> MaintenanceStatus:
    global _maintenance_status
    return _maintenance_status

def update_maintenance_progress(task_name: str, progress: int, message: str, status: Optional[str]=None):
    global _maintenance_status
    with _maintenance_lock:
        task = _maintenance_status.get_task(task_name)
        if task:
            task.progress = progress
            task.message = message
            if status:
                task.status = status
            if status == 'running' and (not task.started_at):
                task.started_at = datetime.now().isoformat()
            if status in ('completed', 'failed'):
                task.completed_at = datetime.now().isoformat()
            total = sum((t.progress for t in _maintenance_status.tasks if t.status != 'pending'))
            count = sum((1 for t in _maintenance_status.tasks if t.status != 'pending'))
            _maintenance_status.overall_progress = total // max(count, 1)

def reset_maintenance_status():
    global _maintenance_status
    with _maintenance_lock:
        for task in _maintenance_status.tasks:
            task.status = 'pending'
            task.progress = 0
            task.message = '等待运行'
            task.started_at = None
            task.completed_at = None
            task.error = None
        _maintenance_status.overall_progress = 0

def _reset_if_not_running():
    global _maintenance_status
    with _maintenance_lock:
        any_running = any((t.status == 'running' for t in _maintenance_status.tasks))
        if not any_running:
            for task in _maintenance_status.tasks:
                task.status = 'pending'
                task.progress = 0
                task.message = '等待运行'
                task.started_at = None
                task.completed_at = None
                task.error = None
            _maintenance_status.overall_progress = 0

class EventChronicleDB:
    """事件纪数据库管理器"""

    def __init__(self, db_path: str='events/event_chronicle.db'):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute("\n                CREATE TABLE IF NOT EXISTS important_events (\n                    id TEXT PRIMARY KEY,\n                    date_string TEXT NOT NULL,\n                    date_precision TEXT NOT NULL\n                        CHECK(date_precision IN ('full','year_month','year_only')),\n                    event_text TEXT NOT NULL,\n                    emotion TEXT DEFAULT '',\n                    created_at TEXT NOT NULL,\n                    updated_at TEXT NOT NULL\n                )\n            ")
            conn.execute("\n                CREATE TABLE IF NOT EXISTS diary_entries (\n                    id TEXT PRIMARY KEY,\n                    date TEXT NOT NULL UNIQUE,\n                    content TEXT NOT NULL,\n                    summary_ids TEXT NOT NULL DEFAULT '[]',\n                    generated_at TEXT NOT NULL,\n                    structured_json TEXT NOT NULL DEFAULT ''\n                )\n            ")
            try:
                conn.execute("ALTER TABLE diary_entries ADD COLUMN structured_json TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            conn.execute('CREATE INDEX IF NOT EXISTS idx_important_events_date ON important_events(date_string)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_diary_entries_date ON diary_entries(date)')
            conn.execute('\n                CREATE TABLE IF NOT EXISTS conversation_days (\n                    date TEXT PRIMARY KEY,\n                    message_count INTEGER DEFAULT 0,\n                    first_message_at TEXT,\n                    last_message_at TEXT\n                )\n            ')
            conn.execute("DELETE FROM conversation_days WHERE date='2020-01-01' AND message_count=0")
            conn.execute("\n                CREATE TABLE IF NOT EXISTS topic_state (\n                    session_id TEXT PRIMARY KEY,\n                    is_active INTEGER DEFAULT 0,\n                    start_time TEXT,\n                    message_count INTEGER DEFAULT 0,\n                    round_count INTEGER DEFAULT 0,\n                    last_activity_time TEXT,\n                    last_message_role TEXT,\n                    drift_detection_start INTEGER DEFAULT 0,\n                    manually_ended INTEGER DEFAULT 0,\n                    ended_at TEXT DEFAULT ''\n                )\n            ")
            conn.execute("\n                CREATE TABLE IF NOT EXISTS conversation_messages (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    active_date TEXT NOT NULL,\n                    calendar_date TEXT NOT NULL DEFAULT '',\n                    session_id TEXT NOT NULL,\n                    timestamp TEXT NOT NULL,\n                    role TEXT NOT NULL,\n                    content TEXT NOT NULL,\n                    reasoning_content TEXT,\n                    message_id TEXT,\n                    is_wander INTEGER DEFAULT 0,\n                    is_sentinel INTEGER DEFAULT 0,\n                    is_reminder INTEGER DEFAULT 0,\n                    tool_summary TEXT,\n                    tool_calls TEXT,\n                    event_parent_id TEXT,\n                    long_text_card TEXT\n                )\n            ")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_msg_date ON conversation_messages(active_date)')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_msg_message_id ON conversation_messages(message_id) WHERE message_id IS NOT NULL')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_msg_session ON conversation_messages(session_id, timestamp)')
            conn.execute('\n                CREATE TABLE IF NOT EXISTS conversation_scenes (\n                    id TEXT PRIMARY KEY,\n                    active_date TEXT NOT NULL,\n                    session_id TEXT,\n                    start_ts TEXT NOT NULL,\n                    end_ts TEXT NOT NULL,\n                    first_user_msg_id TEXT,\n                    last_user_msg_id TEXT,\n                    msg_count INTEGER DEFAULT 0,\n                    user_msg_count INTEGER DEFAULT 0,\n                    k_push_count INTEGER DEFAULT 0,\n                    tool_call_count INTEGER DEFAULT 0,\n                    recollection_count INTEGER DEFAULT 0,\n                    retrieve_count INTEGER DEFAULT 0,\n                    last_retrieved_at TEXT,\n                    created_at TEXT\n                )\n            ')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scenes_date ON conversation_scenes(active_date, start_ts)')
            for col, col_type in [('retrieve_count', 'INTEGER DEFAULT 0'), ('last_retrieved_at', 'TEXT')]:
                try:
                    conn.execute(f'ALTER TABLE conversation_scenes ADD COLUMN {col} {col_type}')
                except sqlite3.OperationalError:
                    pass
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scenes_retrieval ON conversation_scenes(retrieve_count DESC, last_retrieved_at DESC)')
            for col, col_type in [('calendar_date', "TEXT NOT NULL DEFAULT ''"), ('is_wander', 'INTEGER DEFAULT 0'), ('is_sentinel', 'INTEGER DEFAULT 0'), ('is_reminder', 'INTEGER DEFAULT 0'), ('tool_summary', 'TEXT'), ('tool_calls', 'TEXT'), ('event_parent_id', 'TEXT'), ('event_type', "TEXT DEFAULT ''"), ('images', 'TEXT'), ('voice_attachment', 'TEXT'), ('long_text_card', 'TEXT'), ('thinking_chain_json', 'TEXT')]:
                try:
                    conn.execute(f'ALTER TABLE conversation_messages ADD COLUMN {col} {col_type}')
                except sqlite3.OperationalError:
                    pass
            try:
                conn.execute('ALTER TABLE conversation_messages DROP COLUMN is_intermediate')
            except sqlite3.OperationalError:
                pass
            for col, col_type in [('manually_ended', 'INTEGER DEFAULT 0'), ('ended_at', "TEXT DEFAULT ''")]:
                try:
                    conn.execute(f'ALTER TABLE topic_state ADD COLUMN {col} {col_type}')
                except sqlite3.OperationalError:
                    pass
            conn.execute("\n                CREATE TABLE IF NOT EXISTS bookmarks (\n                    id TEXT PRIMARY KEY,\n                    bucket_id TEXT NOT NULL,\n                    original_msg_id TEXT NOT NULL,\n                    session_id TEXT NOT NULL,\n                    original_timestamp TEXT,\n                    collected_by TEXT NOT NULL DEFAULT 'user',\n                    role TEXT DEFAULT 'assistant',\n                    created TEXT NOT NULL\n                )\n            ")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_bookmarks_bucket ON bookmarks(bucket_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_bookmarks_msg_id ON bookmarks(original_msg_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_bookmarks_collected ON bookmarks(collected_by)')
            conn.execute("\n                CREATE TABLE IF NOT EXISTS peer_moments (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    content TEXT NOT NULL,\n                    emotion_tags TEXT DEFAULT '',\n                    source_msg_id INTEGER DEFAULT 0,\n                    created_at TEXT NOT NULL\n                )\n            ")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_em_created ON peer_moments(created_at)')
            conn.execute("\n                CREATE TABLE IF NOT EXISTS message_feedback (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    message_id TEXT NOT NULL,\n                    session_id TEXT DEFAULT '',\n                    feedback_type TEXT NOT NULL CHECK(feedback_type IN ('like', 'dislike')),\n                    reason TEXT DEFAULT '',\n                    created_at TEXT NOT NULL\n                )\n            ")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_mf_message ON message_feedback(message_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_mf_created ON message_feedback(created_at)')
            try:
                conn.execute('ALTER TABLE bookmarks ADD COLUMN content TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE bookmarks ADD COLUMN role TEXT DEFAULT 'assistant'")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def add_important_event(self, event: ImportantEvent) -> str:
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute('INSERT INTO important_events (id,date_string,date_precision,event_text,emotion,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (event.id, event.date_string, event.date_precision, event.event_text, event.emotion, event.created_at, event.updated_at))
                conn.commit()
        return event.id

    def get_important_event(self, event_id: str) -> Optional[ImportantEvent]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM important_events WHERE id=?', (event_id,)).fetchone()
            return self._row_to_important_event(row) if row else None

    def list_important_events(self, limit: int=50, offset: int=0) -> List[ImportantEvent]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM important_events ORDER BY date_string DESC, created_at DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            return [self._row_to_important_event(r) for r in rows]

    def update_important_event(self, event_id: str, updates: Dict[str, Any]) -> bool:
        ALLOWED_COLUMNS = {'date_string', 'date_precision', 'event_text', 'emotion'}
        unknown = set(updates.keys()) - ALLOWED_COLUMNS
        if unknown:
            raise ValueError(f"不允许的字段: {', '.join(sorted(unknown))}")
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                updates['updated_at'] = datetime.now().isoformat()
                set_clause = ', '.join((f'{k}=?' for k in updates))
                cursor = conn.execute(f'UPDATE important_events SET {set_clause} WHERE id=?', list(updates.values()) + [event_id])
                conn.commit()
                return cursor.rowcount > 0

    def delete_important_event(self, event_id: str) -> bool:
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('DELETE FROM important_events WHERE id=?', (event_id,))
                conn.commit()
                return cursor.rowcount > 0

    def get_nearby_important_events(self, today_str: str, days: int=1) -> List[ImportantEvent]:
        today = datetime.strptime(today_str, '%Y-%m-%d')
        yesterday = today - timedelta(days=days)
        day_1 = today.strftime('%Y-%m-%d')
        day_2 = yesterday.strftime('%Y-%m-%d')
        month_prefix = today.strftime('%Y-%m')
        year_prefix = today.strftime('%Y')
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM important_events WHERE\n                   (date_precision='full' AND date_string IN (?, ?))\n                   OR (date_precision='year_month' AND date_string LIKE ?)\n                   OR (date_precision='year_only' AND date_string LIKE ?)\n                   ORDER BY date_string DESC", (day_1, day_2, f'{month_prefix}%', f'{year_prefix}%')).fetchall()
            return [self._row_to_important_event(r) for r in rows]

    def search_important_events(self, keyword: str, limit: int=5) -> List[ImportantEvent]:
        pattern = f'%{keyword}%'
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM important_events WHERE event_text LIKE ? OR emotion LIKE ? ORDER BY date_string DESC LIMIT ?', (pattern, pattern, limit)).fetchall()
            return [self._row_to_important_event(r) for r in rows]

    def list_important_events_all(self) -> List[ImportantEvent]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM important_events ORDER BY date_string DESC').fetchall()
            return [self._row_to_important_event(r) for r in rows]

    def add_diary_entry(self, entry: DiaryEntry) -> str:
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                structured = entry.structured_data or {}
                try:
                    structured_json = json.dumps(structured, ensure_ascii=False) if structured else ''
                except (TypeError, ValueError):
                    structured_json = ''
                old = conn.execute('SELECT id, structured_json FROM diary_entries WHERE date=? LIMIT 1', (entry.date,)).fetchone()
                if old:
                    previous_structured = old[1] or ''
                    if not structured_json:
                        structured_json = previous_structured
                    conn.execute('UPDATE diary_entries SET id=?, content=?, summary_ids=?, generated_at=?, structured_json=? WHERE date=?', (entry.id, entry.content, json.dumps(entry.summary_ids, ensure_ascii=False), entry.generated_at, structured_json, entry.date))
                else:
                    conn.execute('INSERT INTO diary_entries (id,date,content,summary_ids,generated_at,structured_json) VALUES (?,?,?,?,?,?)', (entry.id, entry.date, entry.content, json.dumps(entry.summary_ids, ensure_ascii=False), entry.generated_at, structured_json))
                conn.commit()
        return entry.id

    def get_diary_entry_by_date(self, date: str) -> Optional[DiaryEntry]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM diary_entries WHERE date=?', (date,)).fetchone()
            return self._row_to_diary_entry(row) if row else None

    def get_latest_diary_entry(self) -> Optional[DiaryEntry]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM diary_entries ORDER BY date DESC LIMIT 1').fetchone()
            return self._row_to_diary_entry(row) if row else None

    def list_diary_entries(self, limit: int=30, offset: int=0) -> List[DiaryEntry]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT * FROM diary_entries ORDER BY date DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            return [self._row_to_diary_entry(r) for r in rows]

    def add_conversation_event(self, *, active_date: str, session_id: str, timestamp: str, event_id: str, event_type: str, content: str='', tool_calls=None, parent_message_id: str=None, calendar_date: str='') -> bool:
        """Persist one occurrence in the existing message authority.

        ``parent_message_id`` marks an associated event.  An empty parent
        means the event is independent; the caller must not write a second
        notification or attach the same receipt to an assistant row.
        """
        if not event_id or not event_type or (not session_id) or (not timestamp):
            raise ValueError('event identity, type, session and timestamp are required')
        if tool_calls is not None and (not isinstance(tool_calls, str)):
            tool_calls = json.dumps(tool_calls, ensure_ascii=False)
        return self.add_conversation_message(active_date=active_date, calendar_date=calendar_date or timestamp[:10], session_id=session_id, timestamp=timestamp, role='event', content=content, message_id=event_id, event_type=event_type, tool_calls=tool_calls, event_parent_id=parent_message_id)

    def add_conversation_message(self, active_date: str, session_id: str, timestamp: str, role: str, content: str, reasoning_content: str=None, message_id: str=None, is_wander: int=0, is_sentinel: int=0, is_reminder: int=0, tool_summary: str=None, tool_calls: str=None, event_type: str='', images: str=None, voice_attachment: str=None, thinking_chain_json: str=None, long_text_card: str=None, calendar_date: str='', event_parent_id: str=None):
        """Insert one conversation message and update its scene index.

        Scene maintenance lives here (rather than in each chat/push caller) so
        HTTP, WebSocket, wander, sentinel, reminder, divider, import and other
        writers share exactly one lifecycle hook.  Duplicate message IDs are
        ignored and must not increment scene counters a second time.
        """
        active_date = active_date or str(timestamp or '')[:10]
        calendar_date = calendar_date or active_date
        stored_tool_calls = tool_calls
        tool_events: list[dict] = []
        if role == 'assistant':
            stored_tool_calls, tool_events = _split_new_tool_events(tool_calls, parent_id=message_id)
        stored_tool_summary = None if tool_events else tool_summary
        inserted = False
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('INSERT OR IGNORE INTO conversation_messages (active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, stored_tool_summary, stored_tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id))
                inserted = cursor.rowcount > 0
                if inserted:
                    for event in tool_events:
                        event_cursor = conn.execute('INSERT OR IGNORE INTO conversation_messages (active_date, calendar_date, session_id, timestamp, role, content, message_id, event_type, tool_calls, event_parent_id) VALUES (?,?,?,?,?,?,?,?,?,?)', (active_date, str(event['timestamp'])[:10], session_id, event['timestamp'], 'event', '', event['event_id'], 'tool_event', event['tool_calls'], message_id))
                        if event_cursor.rowcount != 1:
                            raise RuntimeError('tool event identity collision; parent write rolled back')
                conn.commit()
        if inserted:
            try:
                from scene_manager.scene import incremental_update
                incremental_update({'active_date': active_date, 'calendar_date': calendar_date, 'session_id': session_id, 'timestamp': timestamp, 'role': role, 'content': content, 'reasoning_content': reasoning_content, 'message_id': message_id, 'is_wander': is_wander, 'is_sentinel': is_sentinel, 'is_reminder': is_reminder, 'tool_summary': stored_tool_summary, 'tool_calls': stored_tool_calls, 'event_type': event_type, 'images': images, 'voice_attachment': voice_attachment, 'thinking_chain_json': thinking_chain_json, 'event_parent_id': event_parent_id}, chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 增量维护失败（消息已写入）: {exc}')
        return inserted

    def add_messages_batch(self, messages: List[Dict[str, Any]]) -> int:
        """Insert a batch in one transaction and update scenes afterwards.

        ``messages`` contains the same full-field dictionaries accepted by
        :meth:`add_conversation_message`.  The return value is the number of
        rows newly inserted (duplicates are ignored).
        """
        if not messages:
            return 0
        inserted_messages: List[Dict[str, Any]] = []
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                for message in messages:
                    timestamp = message.get('timestamp', '')
                    active_date = message.get('active_date', '') or str(timestamp or '')[:10]
                    session_id = message.get('session_id', '')
                    role = message.get('role', '')
                    content = message.get('content', '')
                    cursor = conn.execute('INSERT OR IGNORE INTO conversation_messages (active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (active_date, message.get('calendar_date', '') or active_date, session_id, timestamp, role, content, message.get('reasoning_content'), message.get('message_id') or message.get('id'), message.get('is_wander', 0) or 0, message.get('is_sentinel', 0) or 0, message.get('is_reminder', 0) or 0, message.get('tool_summary'), message.get('tool_calls'), message.get('event_type', ''), message.get('images'), message.get('voice_attachment'), message.get('thinking_chain_json'), message.get('long_text_card'), message.get('event_parent_id')))
                    if cursor.rowcount > 0:
                        inserted_messages.append(dict(message))
                conn.commit()
        if inserted_messages:
            try:
                from scene_manager.scene import incremental_update
                for message in sorted(inserted_messages, key=lambda item: item.get('timestamp', '')):
                    incremental_update(message, chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 批量增量维护失败（消息已写入）: {exc}')
        return len(inserted_messages)

    def upsert_conversation_message(self, active_date: str, session_id: str, timestamp: str, role: str, content: str, reasoning_content: str=None, message_id: str=None, is_wander: int=0, is_sentinel: int=0, is_reminder: int=0, tool_summary: str=None, tool_calls: str=None, event_type: str='', images: str=None, voice_attachment: str=None, thinking_chain_json: str=None, long_text_card: str=None, calendar_date: str='', event_parent_id: str=None):
        """幂等写入：message_id 存在则更新，不存在则插入。用于 PUT 同步。"""
        if not message_id:
            return
        active_date = active_date or str(timestamp or '')[:10]
        calendar_date = calendar_date or active_date
        previous = self.get_message_by_msg_id(message_id)
        affected_dates = {str(previous.get('active_date') or '')[:10] if previous else '', str(active_date or '')[:10]}
        affected_dates.discard('')
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute("INSERT INTO conversation_messages (active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) WHERE message_id IS NOT NULL DO UPDATE SET active_date=excluded.active_date, calendar_date=excluded.calendar_date, session_id=excluded.session_id, timestamp=excluded.timestamp, role=excluded.role, content=excluded.content, reasoning_content=COALESCE(excluded.reasoning_content, conversation_messages.reasoning_content), is_wander=excluded.is_wander, is_sentinel=excluded.is_sentinel, is_reminder=excluded.is_reminder, tool_summary=COALESCE(excluded.tool_summary, conversation_messages.tool_summary), tool_calls=COALESCE(excluded.tool_calls, conversation_messages.tool_calls), event_type=CASE WHEN excluded.event_type<>'' THEN excluded.event_type ELSE conversation_messages.event_type END, images=COALESCE(excluded.images, conversation_messages.images), voice_attachment=COALESCE(excluded.voice_attachment, conversation_messages.voice_attachment), thinking_chain_json=COALESCE(excluded.thinking_chain_json, conversation_messages.thinking_chain_json), long_text_card=COALESCE(excluded.long_text_card, conversation_messages.long_text_card), event_parent_id=COALESCE(excluded.event_parent_id, conversation_messages.event_parent_id)", (active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id))
                conn.commit()
        try:
            from scene_manager.scene import rebuild_day
            for day in sorted(affected_dates):
                rebuild_day(day, chronicle=self)
        except Exception as exc:
            logger.warning(f'Scene 重算失败（upsert 已写入）: {exc}')

    def delete_conversation_messages(self, message_ids: list) -> int:
        """从 SQLite 删除指定 message_id 的消息。返回删除条数。"""
        if not message_ids:
            return 0
        affected_dates = set()
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                placeholders = ','.join(('?' for _ in message_ids))
                rows = conn.execute(f'SELECT DISTINCT active_date FROM conversation_messages WHERE message_id IN ({placeholders})', message_ids).fetchall()
                affected_dates.update((str(row[0] or '')[:10] for row in rows if row[0]))
                cursor = conn.execute(f'DELETE FROM conversation_messages WHERE message_id IN ({placeholders})', message_ids)
                conn.commit()
                deleted = cursor.rowcount
        if deleted and affected_dates:
            try:
                from scene_manager.scene import rebuild_day
                for day in sorted(affected_dates):
                    rebuild_day(day, chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 删除后重算失败（消息已删除）: {exc}')
        return deleted

    def delete_one_with_associated_events(self, session_id: str, message_id: str) -> dict:
        """SQL-first single-item delete with the event association boundary.

        A linked event has no independent delete route.  Removing its parent
        removes the linked rows in the same transaction; an independent event
        can be deleted alone.  Domain records are deliberately untouched.
        """
        if not session_id or not message_id:
            raise ValueError('session_id and message_id are required')
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                target = conn.execute('SELECT message_id, role, event_parent_id, active_date, event_type, tool_calls FROM conversation_messages WHERE session_id=? AND message_id=?', (session_id, message_id)).fetchone()
                if target is None:
                    return {'deleted_ids': [], 'event_types': [], 'pending_attachments': {'image': [], 'voice': []}}
                if target['role'] == 'event' and target['event_parent_id']:
                    raise ValueError('associated event must be deleted with its parent message')
                rows = [target]
                if target['role'] != 'event':
                    rows.extend(conn.execute("SELECT message_id, role, event_parent_id, active_date, event_type, tool_calls FROM conversation_messages WHERE session_id=? AND role='event' AND event_parent_id=? ORDER BY timestamp, id", (session_id, message_id)).fetchall())
                ids = [str(row['message_id']) for row in rows if row['message_id']]
                placeholders = ','.join(('?' for _ in ids))
                conn.execute(f'DELETE FROM conversation_messages WHERE session_id=? AND message_id IN ({placeholders})', (session_id, *ids))
                conn.commit()
        affected_dates = {str(row['active_date'] or '')[:10] for row in rows if row['active_date']}
        for day in sorted(affected_dates):
            try:
                from scene_manager.scene import rebuild_day
                rebuild_day(day, chronicle=self)
            except Exception as exc:
                logger.warning('Scene 删除后重算失败（消息已删除）: %s', exc)
        return {'deleted_ids': ids, 'event_types': [str(row['event_type'] or '') for row in rows if row['role'] == 'event'], 'pending_attachments': _pending_attachment_ids(rows)}

    def delete_conversation_tail(self, session_id: str, user_message_id: str) -> dict:
        """Atomically revoke a user turn and all later rows, including events.

        This changes only the conversation projection.  External domain
        records and completed physical actions are not rolled back.
        """
        if not session_id or not user_message_id:
            raise ValueError('session_id and user_message_id are required')
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                target = conn.execute('SELECT id, timestamp, role FROM conversation_messages WHERE session_id=? AND message_id=?', (session_id, user_message_id)).fetchone()
                if target is None:
                    return {'deleted_ids': [], 'event_types': [], 'pending_attachments': {'image': [], 'voice': []}}
                if target['role'] != 'user':
                    raise ValueError('revoke target must be a user message')
                predicate = 'session_id=? AND (timestamp>? OR (timestamp=? AND id>=?))'
                params = (session_id, target['timestamp'], target['timestamp'], target['id'])
                rows = conn.execute(f'SELECT message_id, role, active_date, event_type, tool_calls FROM conversation_messages WHERE {predicate} ORDER BY timestamp, id', params).fetchall()
                conn.execute(f'DELETE FROM conversation_messages WHERE {predicate}', params)
                conn.commit()
        for day in sorted({str(row['active_date'] or '')[:10] for row in rows if row['active_date']}):
            try:
                from scene_manager.scene import rebuild_day
                rebuild_day(day, chronicle=self)
            except Exception as exc:
                logger.warning('Scene 撤回后重算失败（消息已删除）: %s', exc)
        return {'deleted_ids': [str(row['message_id']) for row in rows if row['message_id']], 'event_types': [str(row['event_type'] or '') for row in rows if row['role'] == 'event'], 'pending_attachments': _pending_attachment_ids(rows)}

    def delete_conversation_messages_by_session(self, session_id: str, *, exclude_message_ids: Optional[Iterable[str]]=None) -> int:
        """按 session_id 全量删除 SQLite 中的消息。返回删除条数。
        不依赖 message_id 匹配，用于 clear_session_messages 等批量清空场景。
        ``exclude_message_ids`` 用于保留已完成外部副作用对应的 UI 通知。"""
        if not session_id:
            return 0
        excluded = [str(item) for item in exclude_message_ids or [] if item]
        exclusion_sql = ''
        select_params: list[Any] = [session_id]
        delete_params: list[Any] = [session_id]
        if excluded:
            exclusion_sql = f" AND message_id NOT IN ({','.join(('?' for _ in excluded))})"
            select_params.extend(excluded)
            delete_params.extend(excluded)
        affected_dates = set()
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                rows = conn.execute('SELECT DISTINCT active_date FROM conversation_messages WHERE session_id=?' + exclusion_sql, select_params).fetchall()
                affected_dates.update((str(row[0] or '')[:10] for row in rows if row[0]))
                cursor = conn.execute('DELETE FROM conversation_messages WHERE session_id=?' + exclusion_sql, delete_params)
                conn.commit()
                deleted = cursor.rowcount
        if deleted and affected_dates:
            try:
                from scene_manager.scene import rebuild_day
                for day in sorted(affected_dates):
                    rebuild_day(day, chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 清空后重算失败（消息已删除）: {exc}')
        return deleted

    def delete_conversation_messages_since(self, session_id: str, since_timestamp: str, *, exclude_message_ids: Optional[Iterable[str]]=None) -> int:
        """删除指定时间戳之后的消息，可保留真实副作用对应的 UI 事件。"""
        if not session_id or not since_timestamp:
            return 0
        excluded = [str(item) for item in exclude_message_ids or [] if item]
        exclusion_sql = ''
        params: list[Any] = [session_id, since_timestamp]
        if excluded:
            exclusion_sql = f" AND message_id NOT IN ({','.join(('?' for _ in excluded))})"
            params.extend(excluded)
        affected_dates = set()
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                rows = conn.execute(f'SELECT DISTINCT active_date FROM conversation_messages WHERE session_id=? AND timestamp >= ?{exclusion_sql}', params).fetchall()
                affected_dates.update((str(row[0] or '')[:10] for row in rows if row[0]))
                cursor = conn.execute(f'DELETE FROM conversation_messages WHERE session_id=? AND timestamp >= ?{exclusion_sql}', params)
                conn.commit()
                deleted = cursor.rowcount
        if deleted and affected_dates:
            try:
                from scene_manager.scene import rebuild_day
                for day in sorted(affected_dates):
                    rebuild_day(day, chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 撤回后重算失败（消息已删除）: {exc}')
        return deleted

    def update_message_content(self, message_id: str, new_content: str, old_content: str='', old_role: str='', session_id: str='') -> bool:
        """更新 SQLite 中单条消息的内容。返回是否更新成功。
        当 ID 精确匹配失败时，回退到内容相似度匹配（前缀匹配 old_content + role + session_id）。
        """
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('UPDATE conversation_messages SET content=? WHERE message_id=?', (new_content, message_id))
                conn.commit()
                if cursor.rowcount > 0:
                    return True
        if old_content and old_role and session_id:
            prefix = old_content[:100] if len(old_content) >= 20 else old_content
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    cursor = conn.execute('UPDATE conversation_messages SET content=? WHERE session_id=? AND role=? AND content LIKE ? ORDER BY timestamp DESC LIMIT 1', (new_content, session_id, old_role, prefix + '%'))
                    conn.commit()
                    return cursor.rowcount > 0
        return False

    def get_message_by_msg_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """按 message_id 查询单条消息。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id FROM conversation_messages WHERE message_id=? LIMIT 1', (message_id,)).fetchone()
            if row:
                return {'active_date': row['active_date'], 'calendar_date': row['calendar_date'], 'role': row['role'], 'content': row['content'], 'timestamp': row['timestamp'], 'session_id': row['session_id'], 'reasoning_content': row['reasoning_content'], 'message_id': row['message_id'], 'is_wander': row['is_wander'], 'is_sentinel': row['is_sentinel'], 'is_reminder': row['is_reminder'], 'tool_summary': row['tool_summary'], 'tool_calls': row['tool_calls'], 'event_type': row['event_type'], 'event_parent_id': row['event_parent_id'], 'images': row['images'], 'voice_attachment': row['voice_attachment'], 'thinking_chain_json': row['thinking_chain_json']}
            return None

    def update_message_content(self, message_id: str, content: str) -> bool:
        """按 message_id 更新消息内容（edit_message 裂隙修复）。"""
        if not message_id or not content:
            return False
        previous = self.get_message_by_msg_id(message_id)
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('UPDATE conversation_messages SET content=? WHERE message_id=?', (content, message_id))
                conn.commit()
                updated = cursor.rowcount > 0
        if updated and previous and previous.get('active_date'):
            try:
                from scene_manager.scene import rebuild_day
                rebuild_day(previous['active_date'], chronicle=self)
            except Exception as exc:
                logger.warning(f'Scene 编辑后重算失败（消息已更新）: {exc}')
        return updated

    def update_message_tool_calls(self, message_id: str, tool_calls_json: str) -> bool:
        """按 message_id 更新 tool_calls 列（语音注入 voice_path 后同步 SQLite 用）。

        必须用 UPDATE 而非 INSERT OR REPLACE——REPLACE 会清空 content/voice_attachment 等其它列。
        """
        if not message_id:
            return False
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('UPDATE conversation_messages SET tool_calls=? WHERE message_id=?', (tool_calls_json, message_id))
                conn.commit()
            return cursor.rowcount > 0

    def update_attachment_if_pending(self, session_id: str, message_id: str, tool_name: str, identity_key: str, identity_value: str, tool_calls_json: str, *, require_pending: bool=True, replacement_identity_value: Optional[str]=None) -> bool:
        """CAS fence, optionally rotating the ID after checking the old owner.

        Completion keeps the same identity by default. An explicit retry may
        replace it; its expected current identity still guards against stale
        retries, deleted messages and late completion of the old generation.
        """
        if not all((session_id, message_id, tool_name, identity_key, identity_value)):
            return False
        proposed_identity = identity_value if replacement_identity_value is None else replacement_identity_value
        if not proposed_identity:
            return False
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                row = conn.execute("SELECT tool_calls FROM conversation_messages WHERE session_id=? AND message_id=? AND role='assistant'", (session_id, message_id)).fetchone()
                if row is None:
                    return False
                try:
                    calls = json.loads(row[0] or '[]')
                except (TypeError, ValueError):
                    return False
                if not isinstance(calls, list):
                    return False
                try:
                    proposed_calls = json.loads(tool_calls_json)
                except (TypeError, ValueError):
                    return False
                if not isinstance(proposed_calls, list):
                    return False

                def matches(call, expected_identity):
                    return isinstance(call, dict) and call.get('tool') == tool_name and isinstance(call.get('extra_data'), dict) and (str(call['extra_data'].get(identity_key) or '') == expected_identity)
                proposed = next((call for call in proposed_calls if matches(call, proposed_identity)), None)
                if proposed is None:
                    return False
                current_index = next((index for index, call in enumerate(calls) if matches(call, identity_value) and (not require_pending or call['extra_data'].get('attachment_status') == 'pending')), None)
                if current_index is None:
                    return False
                calls[current_index] = proposed
                cursor = conn.execute('UPDATE conversation_messages SET tool_calls=? WHERE session_id=? AND message_id=?', (json.dumps(calls, ensure_ascii=False), session_id, message_id))
                conn.commit()
                return cursor.rowcount == 1

    def update_message_long_text_card(self, message_id: str, long_text_card: str) -> bool:
        """原地更新从属卡片；调用方负责同步 session JSON。"""
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cursor = conn.execute('UPDATE conversation_messages SET long_text_card=? WHERE message_id=?', (long_text_card, message_id))
                conn.commit()
                return cursor.rowcount > 0

    def get_messages_by_date(self, active_date: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id FROM conversation_messages WHERE active_date=? ORDER BY timestamp, id', (active_date,)).fetchall()
            return [{'active_date': r['active_date'], 'calendar_date': r['calendar_date'], 'role': r['role'], 'content': r['content'], 'timestamp': r['timestamp'], 'session_id': r['session_id'], 'reasoning_content': r['reasoning_content'], 'message_id': r['message_id'], 'is_wander': r['is_wander'], 'is_sentinel': r['is_sentinel'], 'is_reminder': r['is_reminder'], 'tool_summary': r['tool_summary'], 'tool_calls': r['tool_calls'], 'event_type': r['event_type'], 'images': r['images'], 'voice_attachment': r['voice_attachment'], 'thinking_chain_json': r['thinking_chain_json'], 'long_text_card': r['long_text_card'], 'event_parent_id': r['event_parent_id']} for r in rows]

    def get_messages_by_date_range(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """按日期范围查询消息。用于 _consolidate_topic_memory 等需要跨天读取的场景。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT active_date, calendar_date, session_id, timestamp, role, content, reasoning_content, message_id, is_wander, is_sentinel, is_reminder, tool_summary, tool_calls, event_type, images, voice_attachment, thinking_chain_json, long_text_card, event_parent_id FROM conversation_messages WHERE active_date >= ? AND active_date <= ? ORDER BY timestamp, id', (start_date, end_date)).fetchall()
            return [{'active_date': r['active_date'], 'calendar_date': r['calendar_date'], 'role': r['role'], 'content': r['content'], 'timestamp': r['timestamp'], 'session_id': r['session_id'], 'reasoning_content': r['reasoning_content'], 'message_id': r['message_id'], 'is_wander': r['is_wander'], 'is_sentinel': r['is_sentinel'], 'is_reminder': r['is_reminder'], 'tool_summary': r['tool_summary'], 'tool_calls': r['tool_calls'], 'event_type': r['event_type'], 'images': r['images'], 'voice_attachment': r['voice_attachment'], 'thinking_chain_json': r['thinking_chain_json'], 'long_text_card': r['long_text_card'], 'event_parent_id': r['event_parent_id']} for r in rows]

    def find_tool_attachment_messages(self, session_id: str, generation_id: str) -> List[Dict[str, Any]]:
        """Locate attachment candidates without a recent-message window; caller checks exact JSON identity."""
        if len(generation_id) != 32 or any((c not in '0123456789abcdef' for c in generation_id)):
            return []
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute('SELECT message_id, tool_calls FROM conversation_messages WHERE session_id=? AND instr(tool_calls, ?) > 0 ORDER BY timestamp DESC', (session_id, generation_id)).fetchall()
        return [dict(row) for row in rows]

    def get_messages_by_session_id(self, session_id: str, limit: int=100, desc: bool=False) -> List[Dict[str, Any]]:
        """按 session_id 查询消息。desc=True 时取最近 N 条（时间倒序后反转回正序）。"""
        return self._query_messages(session_id=session_id, limit=limit, desc=desc)

    def count_messages_by_session_id(self, session_id: str) -> int:
        """只查索引计数，不读取消息正文。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute('SELECT COUNT(*) FROM conversation_messages WHERE session_id=?', (session_id,)).fetchone()
        return int(row[0]) if row else 0

    def get_messages_before(self, session_id: str, limit: int=200, before_id: str=None, before_ts: str=None, before_row_id: int=0) -> Tuple[List[Dict[str, Any]], bool, bool]:
        """全序 (timestamp, id) 游标分页：取尾窗，或游标之前的窗口。

        返回 (messages, has_more, cursor_stale)：
        - messages      按时间正序排列的窗口，格式与 get_messages_by_session_id 一致
        - has_more      True 表示服务端还有更早的消息
        - cursor_stale  True 表示游标已失效（游标消息被撤回/删除），
                        调用方应重置游标并重拉尾窗，不要继续往前翻

        为什么必须带 rowid 做 tiebreak：conversation_messages 的 timestamp 存在大量
        同秒并列（生产数据中最极端的一个 timestamp 挤了 19 行）。只按 timestamp 排序时，
        同秒内各行的相对顺序在两次查询之间没有保证，翻页会跳消息（永久丢失）或重复。
        判据与 delete_conversation_tail 保持一致：(timestamp, id) 构成全序。
        """
        if not session_id or limit <= 0:
            return ([], False, False)
        cursor_ts: Optional[str] = None
        cursor_id: Optional[int] = None
        inclusive = False
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            if before_id:
                row = conn.execute('SELECT id, timestamp FROM conversation_messages WHERE session_id=? AND message_id=? ORDER BY id LIMIT 1', (session_id, before_id)).fetchone()
                if row is not None:
                    cursor_ts, cursor_id = (row['timestamp'], row['id'])
                elif before_ts and before_row_id > 0:
                    cursor_ts, cursor_id = (before_ts, before_row_id)
                elif before_ts:
                    cursor_ts, inclusive = (before_ts, True)
                else:
                    return ([], False, True)
            elif before_ts:
                cursor_ts = before_ts
            if cursor_ts is None:
                sql = f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? ORDER BY timestamp DESC, id DESC LIMIT ?'
                params: tuple = (session_id, limit + 1)
            elif cursor_id is None:
                op = '<=' if inclusive else '<'
                sql = f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND timestamp {op} ? ORDER BY timestamp DESC, id DESC LIMIT ?'
                params = (session_id, cursor_ts, limit + 1)
            else:
                sql = f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND (timestamp < ? OR (timestamp = ? AND id < ?)) ORDER BY timestamp DESC, id DESC LIMIT ?'
                params = (session_id, cursor_ts, cursor_ts, cursor_id, limit + 1)
            rows = conn.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return ([{k: r[k] for k in _MSG_KEYS} for r in reversed(rows)], has_more, False)

    def get_messages_after(self, session_id: str, after_id: str, after_ts: str='', limit: int=200, after_row_id: int=0) -> Tuple[List[Dict[str, Any]], bool, bool]:
        """定位到旧窗口后，按同一全序向较新的消息翻页。"""
        if not session_id or not after_id or limit <= 0:
            return ([], False, True)
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            anchor = conn.execute('SELECT id, timestamp FROM conversation_messages WHERE session_id=? AND message_id=? LIMIT 1', (session_id, after_id)).fetchone()
            if anchor is not None:
                rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND (timestamp > ? OR (timestamp = ? AND id > ?)) ORDER BY timestamp, id LIMIT ?', (session_id, anchor['timestamp'], anchor['timestamp'], anchor['id'], limit + 1)).fetchall()
            elif after_ts and after_row_id > 0:
                rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND (timestamp > ? OR (timestamp = ? AND id > ?)) ORDER BY timestamp, id LIMIT ?', (session_id, after_ts, after_ts, after_row_id, limit + 1)).fetchall()
            elif after_ts:
                rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND timestamp >= ? ORDER BY timestamp, id LIMIT ?', (session_id, after_ts, limit + 1)).fetchall()
            else:
                return ([], False, True)
        has_more = len(rows) > limit
        return ([{k: row[k] for k in _MSG_KEYS} for row in rows[:limit]], has_more, False)

    def search_session_messages(self, session_id: str, query: str='', limit: int=50, before_ts: str='', before_row_id: int=0, message_type: str='all') -> Dict[str, Any]:
        """Search persisted text/attachments with a stable, bounded cursor page."""
        from message_search import search_messages
        return search_messages(self.db_path, session_id, query, limit, before_ts, before_row_id, message_type)

    def get_messages_around(self, session_id: str, message_id: str, before: int=80, after: int=80) -> Optional[Dict[str, Any]]:
        """按精确消息 ID 取连续窗口，搜索定位无需从尾部逐页加载。"""
        before = max(0, min(int(before), 100))
        after = max(0, min(int(after), 100))
        with closing(sqlite3.connect(self.db_path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            target = conn.execute(f'SELECT id, {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND message_id=? LIMIT 1', (session_id, message_id)).fetchone()
            if target is None:
                return None
            ts, row_id = (target['timestamp'], target['id'])
            earlier = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND (timestamp < ? OR (timestamp = ? AND id < ?)) ORDER BY timestamp DESC, id DESC LIMIT ?', (session_id, ts, ts, row_id, before + 1)).fetchall()
            later = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND (timestamp > ? OR (timestamp = ? AND id > ?)) ORDER BY timestamp, id LIMIT ?', (session_id, ts, ts, row_id, after + 1)).fetchall()
        return {'messages': [{k: r[k] for k in _MSG_KEYS} for r in reversed(earlier[:before])] + [{k: target[k] for k in _MSG_KEYS}] + [{k: r[k] for k in _MSG_KEYS} for r in later[:after]], 'has_more_before': len(earlier) > before, 'has_more_after': len(later) > after}

    def get_message_context_by_id(self, session_id: str, message_id: str, before: int=3, after: int=3):
        """通过 message_id 精确定位一条消息，并返回其前后上下文。
        用于书签上下文快照，免去加载全部消息的开销。
        返回: (messages_before, target_message, messages_after) 或 (None, None, None)
        """
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            target = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND message_id=? LIMIT 1', (session_id, message_id)).fetchone()
            if not target:
                return (None, None, None)
            target_ts = target['timestamp']
            target_dict = dict(target)
            before_rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND timestamp < ? ORDER BY timestamp DESC LIMIT ?', (session_id, target_ts, before)).fetchall()
            messages_before = [dict(r) for r in reversed(before_rows)]
            after_rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? AND timestamp > ? ORDER BY timestamp ASC LIMIT ?', (session_id, target_ts, after)).fetchall()
            messages_after = [dict(r) for r in after_rows]
            return (messages_before, target_dict, messages_after)

    def _query_messages(self, session_id: str=None, limit: int=100, desc: bool=False) -> List[Dict[str, Any]]:
        """内部通用消息查询。session_id 为空时返回空列表。"""
        if not session_id:
            return []
        order = 'DESC' if desc else 'ASC'
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f'SELECT {_MSG_COLS} FROM conversation_messages WHERE session_id=? ORDER BY timestamp {order} LIMIT ?', (session_id, limit)).fetchall()
            if desc:
                rows = list(reversed(rows))
            return [{k: r[k] for k in _MSG_KEYS} for r in rows]

    def get_recent_active_sessions(self, limit: int=30) -> List[str]:
        """获取最近有消息的 session_id 列表（按最后消息时间倒序）。
        用于书签上下文全库回退搜索。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute('SELECT DISTINCT session_id FROM conversation_messages ORDER BY timestamp DESC LIMIT ?', (limit,)).fetchall()
            return [r[0] for r in rows]

    def add_bookmark(self, bucket_id: str, original_msg_id: str, session_id: str, original_timestamp: str='', collected_by: str='user', content: str='', role: str='assistant') -> str:
        """新增书签元数据。返回 bookmark id。"""
        import uuid
        bid = f'bm_{uuid.uuid4().hex[:12]}'
        created = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute('INSERT INTO bookmarks (id, bucket_id, original_msg_id, session_id, original_timestamp, collected_by, created, content, role) VALUES (?,?,?,?,?,?,?,?,?)', (bid, bucket_id, original_msg_id, session_id, original_timestamp, collected_by, created, content[:5000], role))
                conn.commit()
        return bid

    def get_bookmark_meta_by_bucket(self, bucket_id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM bookmarks WHERE bucket_id=? LIMIT 1', (bucket_id,)).fetchone()
            return dict(row) if row else None

    def get_bookmark_meta_by_msg_id(self, original_msg_id: str) -> Optional[Dict[str, Any]]:
        """用于去重：查某条消息是否已被收藏。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM bookmarks WHERE original_msg_id=? LIMIT 1', (original_msg_id,)).fetchone()
            return dict(row) if row else None

    def list_bookmarks(self, collected_by: str=None, offset: int=0, limit: int=20) -> tuple:
        """列出书签元数据。返回 (items, total)。"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            if collected_by:
                total_row = conn.execute('SELECT COUNT(*) as cnt FROM bookmarks WHERE collected_by=?', (collected_by,)).fetchone()
                rows = conn.execute('SELECT * FROM bookmarks WHERE collected_by=? ORDER BY created DESC LIMIT ? OFFSET ?', (collected_by, limit, offset)).fetchall()
            else:
                total_row = conn.execute('SELECT COUNT(*) as cnt FROM bookmarks').fetchone()
                rows = conn.execute('SELECT * FROM bookmarks ORDER BY created DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            return ([dict(r) for r in rows], total_row['cnt'] if total_row else 0)

    def delete_bookmark_meta(self, bucket_id: str) -> bool:
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                cur = conn.execute('DELETE FROM bookmarks WHERE bucket_id=?', (bucket_id,))
                conn.commit()
                return cur.rowcount > 0

    def update_bookmark_meta(self, bucket_id: str, updates: dict) -> bool:
        """更新书签元数据（如补全 original_msg_id）。
        注意：session_id 必须在白名单内——ensure-context 跨 session 定位后需同步更新
        session_id，否则精确匹配 WHERE session_id=? AND message_id=? 永久失配。"""
        allowed = {'original_msg_id', 'original_timestamp', 'session_id', 'content'}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return False
        try:
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    set_clause = ', '.join((f'{k}=?' for k in filtered))
                    values = list(filtered.values()) + [bucket_id]
                    cur = conn.execute(f'UPDATE bookmarks SET {set_clause} WHERE bucket_id=?', values)
                    conn.commit()
                    return cur.rowcount > 0
        except Exception:
            return False

    def migrate_bookmarks_from_ob_rev(self, ob_buckets: list) -> dict:
        """
        从 OB_Rev bucket 列表迁移书签到 bookmarks 表，并自动去重。
        ob_buckets: client.bucket_mgr.list_all() 返回的 bucket 列表。
        返回 {"imported": N, "deduped": N}。
        """
        import uuid as _uuid
        imported = 0
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                existing = set((row[0] for row in conn.execute('SELECT bucket_id FROM bookmarks').fetchall()))
                for b in ob_buckets:
                    meta = b.get('metadata', {})
                    tags = meta.get('tags', []) or []
                    if 'collection' not in tags:
                        continue
                    bucket_id = meta.get('id', '')
                    if not bucket_id or bucket_id in existing:
                        continue
                    collected_by = 'k' if 'collected_by_k' in tags else 'user'
                    bid = f'bm_{_uuid.uuid4().hex[:12]}'
                    created = meta.get('created', datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
                    content_val = b.get('content', '') or meta.get('content', '')
                    conn.execute('INSERT INTO bookmarks (id, bucket_id, original_msg_id, session_id, original_timestamp, collected_by, created, content) VALUES (?,?,?,?,?,?,?,?)', (bid, bucket_id, meta.get('original_msg_id', ''), meta.get('session_id', ''), meta.get('original_timestamp', ''), collected_by, created, (content_val or '')[:500]))
                    existing.add(bucket_id)
                    imported += 1
                conn.commit()
        deduped, dormant_ids = self._dedup_bookmarks_by_content(ob_buckets)
        return {'imported': imported, 'deduped': deduped, 'dormant_ids': dormant_ids}

    def _dedup_bookmarks_by_content(self, ob_buckets: list) -> tuple:
        """
        按内容去重书签。相同内容只保留最新，旧条目从 bookmarks 表删除。
        返回 (deduped_count, [需标记为 dormant 的 bucket_id 列表])。
        """
        removed = 0
        dormant_ids = []
        try:
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=10) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute('SELECT id, bucket_id, collected_by, created FROM bookmarks ORDER BY created DESC').fetchall()
                bucket_map = {}
                for b in ob_buckets:
                    bid = (b.get('metadata') or {}).get('id', '')
                    if bid:
                        bucket_map[bid] = b.get('content', '')[:150].strip()
                seen = {}
                to_remove = []
                for row in rows:
                    content = bucket_map.get(row['bucket_id'], '') or ''
                    ck = content.replace(' ', '').replace('\n', '') if content else row['bucket_id']
                    if ck in seen:
                        to_remove.append((row['bucket_id'], row['id']))
                    else:
                        seen[ck] = True
                if to_remove:
                    with sqlite3.connect(self.db_path, timeout=10) as conn:
                        for bucket_id, bm_id in to_remove:
                            conn.execute('DELETE FROM bookmarks WHERE id=?', (bm_id,))
                            removed += 1
                        conn.commit()
                    dormant_ids = [bid for bid, _ in to_remove]
        except Exception:
            pass
        return (removed, dormant_ids)

    def upsert_conversation_day(self, date: str, count: int, first_at: str=None, last_at: str=None):
        with self._lock:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.execute('INSERT OR REPLACE INTO conversation_days (date, message_count, first_message_at, last_message_at) VALUES (?,?,?,?)', (date, count, first_at, last_at))
                conn.commit()

    def get_topic_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """读取话题状态"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM topic_state WHERE session_id=?', (session_id,)).fetchone()
            return dict(row) if row else None

    def save_topic_state(self, session_id: str, state: Dict[str, Any]) -> None:
        """保存话题状态（UPSERT）"""
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute('INSERT OR REPLACE INTO topic_state (session_id, is_active, start_time, message_count, round_count, last_activity_time, last_message_role, drift_detection_start, manually_ended, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (session_id, state.get('is_active', 0), state.get('start_time', ''), state.get('message_count', 0), state.get('round_count', 0), state.get('last_activity_time', ''), state.get('last_message_role', ''), state.get('drift_detection_start', 0), state.get('manually_ended', 0), state.get('ended_at', '')))
            conn.commit()

    def get_last_active_date(self, before_date: str=None) -> Optional[str]:
        """返回 before_date 之前最后一个有消息的日期（从 conversation_messages 实时聚合）。"""
        if before_date is None:
            before_date = now_date()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute("SELECT active_date FROM conversation_messages WHERE active_date < ? AND (role IS NULL OR role != 'notification') GROUP BY active_date HAVING COUNT(*) > 0 ORDER BY active_date DESC LIMIT 1", (before_date,)).fetchone()
            return row[0] if row else None

    def list_active_dates(self, before_date: str=None, limit: int=90) -> List[str]:
        """列出有真实 user/assistant 原文的历史 active_date。

        This is a read-only companion to ``get_messages_by_date`` for bounded
        random-day recall.  UI-only/system/wander rows do not make an otherwise
        empty day eligible, and today's date is excluded by the caller's
        ``before_date`` boundary.
        """
        if before_date is None:
            before_date = now_date()
        try:
            limit = max(1, min(int(limit), 365))
        except (TypeError, ValueError):
            limit = 90
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute("SELECT active_date FROM conversation_messages WHERE active_date < ? AND role IN ('user', 'assistant') AND TRIM(COALESCE(content, '')) <> '' AND COALESCE(is_wander, 0)=0 AND COALESCE(is_sentinel, 0)=0 AND COALESCE(is_reminder, 0)=0 GROUP BY active_date ORDER BY active_date DESC LIMIT ?", (str(before_date)[:10], limit)).fetchall()
        return [str(row[0])[:10] for row in rows if row and row[0]]

    def get_nth_previous_active_date(self, n: int, before_date: str=None) -> Optional[str]:
        """返回 before_date 之前第 n 个有消息的日期（实时聚合，不依赖 conversation_days 表）。"""
        if before_date is None:
            before_date = now_date()
        if n < 1:
            return None
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute("SELECT active_date FROM conversation_messages WHERE active_date < ? AND (role IS NULL OR role != 'notification') GROUP BY active_date HAVING COUNT(*) > 0 ORDER BY active_date DESC LIMIT 1 OFFSET ?", (before_date, n - 1)).fetchone()
            return row[0] if row else None

    @staticmethod
    def _row_to_important_event(row) -> Optional[ImportantEvent]:
        if not row:
            return None
        return ImportantEvent(id=row['id'], date_string=row['date_string'], date_precision=row['date_precision'], event_text=row['event_text'], emotion=row['emotion'], created_at=row['created_at'], updated_at=row['updated_at'])

    @staticmethod
    def _row_to_diary_entry(row) -> Optional[DiaryEntry]:
        if not row:
            return None
        try:
            keys = set(row.keys())
        except AttributeError:
            keys = set()
        structured_raw = row['structured_json'] if 'structured_json' in keys else ''
        structured_data: Dict[str, Any] = {}
        if structured_raw:
            try:
                parsed = json.loads(structured_raw) if isinstance(structured_raw, str) else structured_raw
                if isinstance(parsed, dict):
                    structured_data = parsed
            except (TypeError, ValueError):
                structured_data = {}
        summary_ids = []
        try:
            raw_summary_ids = row['summary_ids']
            parsed_summary_ids = json.loads(raw_summary_ids) if raw_summary_ids else []
            if isinstance(parsed_summary_ids, list):
                summary_ids = parsed_summary_ids
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            summary_ids = []
        return DiaryEntry(id=row['id'], date=row['date'], content=row['content'], summary_ids=summary_ids, generated_at=row['generated_at'], structured_data=structured_data)
_global_chronicle: Optional[EventChronicleDB] = None

def get_global_chronicle() -> EventChronicleDB:
    global _global_chronicle
    if _global_chronicle is None:
        _global_chronicle = EventChronicleDB()
        logger.info('事件纪数据库已初始化')
    return _global_chronicle

def _to_local(iso_str: str) -> str:
    """将任意 ISO 时间戳转为北京时间字符串（委托 time_utils.to_beijing_time）"""
    return to_beijing_time(iso_str)

def _read_all_messages_for_date(target_date: str, bleed: bool=True) -> list:
    """读取目标日期的所有消息（SQLite 单源）。
    date 列在存储时由 active_date 显式设置，午夜消息自动归属到正确的活跃日。
    bleed 参数仅保留调用兼容；午夜延续已由 active_date 归属，不再拼接相邻活跃日。
    """
    all_messages = []
    seen = set()
    try:
        chronicle = get_global_chronicle()
        from context_builder.event_history_adapter import adapt_history_for_context
        msgs = adapt_history_for_context(chronicle.get_messages_by_date(target_date))
        for m in msgs:
            local_ts = _to_local(m.get('timestamp', ''))
            role = m.get('role', '')
            if role not in ('user', 'assistant', 'system'):
                continue
            content = m.get('content', '')
            if role == 'user' and m.get('long_text_card'):
                from memory_v2.source_projection import SOURCE_PROJECTION_START_ACTIVE_DATE, long_text_card_fact
                card_fact = long_text_card_fact(m.get('long_text_card')) if target_date >= SOURCE_PROJECTION_START_ACTIVE_DATE else ''
                if card_fact:
                    content = f'{content}\n{card_fact}' if content else card_fact
            if (not content or len(content.strip()) < 2) and (not (role == 'assistant' and m.get('tool_calls'))):
                continue
            source_message_id = str(m.get('message_id') or m.get('id') or '').strip()
            key = source_message_id or (role, local_ts[:19], content[:50])
            if key not in seen:
                seen.add(key)
                va_raw = m.get('voice_attachment')
                va = None
                if va_raw:
                    try:
                        va = json.loads(va_raw) if isinstance(va_raw, str) else va_raw
                    except (ValueError, TypeError):
                        va = None
                all_messages.append({'role': role, 'content': content, 'timestamp': local_ts, 'message_id': source_message_id, 'is_wander': m.get('is_wander', 0), 'is_sentinel': m.get('is_sentinel', 0), 'is_reminder': m.get('is_reminder', 0), 'event_type': str(m.get('event_type') or ''), 'notification_projection': bool(m.get('notification_projection')), 'tool_calls': m.get('tool_calls'), 'voice_attachment': va})
    except Exception as e:
        logger.error(f'_read_all_messages_for_date 失败 (target={target_date}): {e}')
        raise RuntimeError(f'日记原料读取失败：{target_date}') from e
    all_messages = [m for m in all_messages if str(m.get('content') or '').strip() or (m.get('tool_calls') and m.get('tool_calls') != '[]')]
    return all_messages

def diary_source_manifest(messages: list[dict]) -> dict[str, Any]:
    """Body-free receipt for the exact projected messages used by a diary."""
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':'))
    return {'version': 1, 'count': len(messages), 'digest': hashlib.sha256(payload.encode('utf-8')).hexdigest()}

def diary_source_status(entry: DiaryEntry, target_date: str) -> str:
    """Return current, reviewed, stale or unknown; read errors propagate."""
    if (getattr(entry, 'structured_data', None) or {}).get('_source_review'):
        from diary_source_audit import source_snapshot, review_matches
        if review_matches(entry, source_snapshot(target_date)):
            return 'reviewed'
    manifest = (getattr(entry, 'structured_data', None) or {}).get('_source_manifest')
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        return 'unknown'
    if manifest != diary_source_manifest(_read_all_messages_for_date(target_date)):
        return 'stale'
    body_digest = (getattr(entry, 'structured_data', None) or {}).get('_body_source_digest')
    if body_digest is not None:
        from silicon_perception.analysis.daily_summary import sentinel_diary_section
        current = hashlib.sha256((sentinel_diary_section(target_date) or '').encode('utf-8')).hexdigest()
        if body_digest != current:
            return 'stale'
    return 'current'

def _read_yesterday_messages_formatted(target_date: str=None) -> Optional[str]:
    """读取上一个活跃日的完整对话记录，返回格式化文本。无消息返回 None。
    target_date 为空时自动查询 get_last_active_date()，否则使用指定日期。
    """
    if target_date is None:
        try:
            target_date = get_global_chronicle().get_last_active_date()
        except Exception:
            target_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    if not target_date:
        return None
    msgs = _read_all_messages_for_date(target_date, bleed=True)
    if not msgs:
        return None
    lines = []
    for m in msgs:
        is_user = m['role'] == 'user'
        is_wander = m.get('is_wander') or m.get('isWander')
        is_sentinel = m.get('is_sentinel') or m.get('isSentinel')
        is_reminder = m.get('is_reminder') or m.get('isReminder')
        ts_raw = m['timestamp']
        if 'T' in ts_raw:
            ts = f'{ts_raw[5:7]}/{ts_raw[8:10]} {ts_raw[11:16]}'
        else:
            ts = ts_raw[:16]
        content = m['content']
        if m.get('event_type') == 'wander_activity' and (not m.get('notification_projection')):
            from context_builder.wander_event_projection import format_wander_activity_event
            content = format_wander_activity_event(content, m.get('tool_calls'), str(m.get('message_id') or m.get('id') or ''), level='experience')
        if not is_user:
            from tool_action_protocol import strip_tool_protocol_text
            content = strip_tool_protocol_text(content)
        if m.get('event_type') == 'rift_game':
            content = f'[裂隙档案·虚构共同游玩] {content}'
        va = m.get('voice_attachment')
        tone = (va or {}).get('tone') if isinstance(va, dict) else None
        voice_marker = f'（语音·{tone}）' if tone else '（语音）' if va else ''
        if is_user:
            lines.append(f'[{ts}] 人类伙伴说{voice_marker}：{content}')
        elif m['role'] == 'system':
            lines.append(f'[{ts}] [群聊摘要] {content[:300]}')
        elif is_sentinel:
            lines.append(f'[{ts}] AI🛡️：{content}')
        elif is_reminder:
            lines.append(f'[{ts}] AI⏰：{content}')
        elif is_wander:
            lines.append(f'[{ts}] AI💭：{content}')
        else:
            lines.append(f'[{ts}] AI 回应：{content}')
        from context_builder.text_utils import build_tool_history_fact
        action_fact = build_tool_history_fact(m['role'], None, m.get('tool_calls'), m.get('event_type', ''))
        if action_fact:
            lines.append(action_fact)
    text = '\n'.join(lines)
    _ycap = get_truncation_limit('yesterday_cap')
    if _ycap > 0 and len(text) > _ycap:
        text = text[-_ycap:]
    return text

def _build_diary_prompt_from_messages(messages: list, target_date: str, active_todos_text: str='', yesterday_diary: str='', sentinel_summary: str='') -> str:
    """Build AI's subjective day-end reflection from authoritative messages.

    Memory V2 owns factual segmentation and event continuity.  The diary is
    deliberately not a second event timeline, TODO list or open-loop store.
    ``active_todos_text`` remains in the call signature for old callers but
    is intentionally excluded from the prompt.
    """
    del active_todos_text
    from api_sources.model_context import enrich_rows
    from model_provenance import PROVENANCE_GUIDE
    messages = enrich_rows(messages)
    if not messages:
        return ''
    lines = []
    for m in messages:
        t = m.get('timestamp', '')
        hhmm = t[11:16] if len(t) > 16 else '??:??'
        is_wander = m.get('is_wander', 0) == 1 or m.get('is_wander') is True
        is_sentinel = m.get('is_sentinel', 0) == 1 or m.get('is_sentinel') is True
        is_reminder = m.get('is_reminder', 0) == 1 or m.get('is_reminder') is True
        if m.get('event_type') == 'model_switch':
            role = '【模型切换运行事件；不是人类伙伴或AI的发言】'
        elif m['role'] == 'system' and m.get('event_type') == 'tool_event':
            role = '【工具事件】'
        elif m['role'] == 'system' and m.get('event_type') == 'wander_activity':
            role = '【漫想事件·结算感受；未分享不等于未发生】'
        elif m['role'] == 'system' and m.get('notification_projection'):
            role = '【事件事实】'
        elif m['role'] == 'user':
            role = '【人类伙伴说】'
        elif is_wander:
            role = '【漫想推送】'
        elif is_sentinel:
            role = '【哨兵推送】'
        elif is_reminder:
            role = '【提醒推送】'
        else:
            role = '【我回应】'
        if m.get('event_type') == 'rift_game':
            role = f'{role}【裂隙档案·虚构共同游玩】'
        content = m['content']
        if m.get('event_type') == 'wander_activity' and (not m.get('notification_projection')):
            from context_builder.wander_event_projection import format_wander_activity_event
            content = format_wander_activity_event(content, m.get('tool_calls'), str(m.get('message_id') or m.get('id') or ''), level='experience')
        _dmsg = get_truncation_limit('diary_msg_cap')
        if _dmsg > 0 and len(content) > _dmsg:
            content = content[:_dmsg] + '…'
        source_id = str(m.get('message_id') or '').strip()
        anchor = f' [message_id={source_id}]' if source_id else ''
        if m.get('role') == 'assistant' and m.get('generation_model'):
            role += f"【生成模型：{m['generation_model']}】"
        lines.append(f'{hhmm}{anchor} {role} {content}')
    conv_text = '\n'.join(lines)
    sentinel_reference = f'以下是当天的感知摘要。它只说明你看见过什么；若你没有在【我回应】、【漫想推送】或【漫想事件·结算感受】中形成感受，不要替自己补写感受：\n{sentinel_summary}\n' if sentinel_summary else ''
    _dtot = get_truncation_limit('diary_total_cap')
    if _dtot > 0 and len(conv_text) > _dtot:
        head = lines[:len(lines) * 3 // 10]
        tail = lines[-len(lines) * 3 // 10:]
        skipped = len(lines) - len(head) - len(tail)
        conv_text = '\n'.join(head) + f'\n...省略 {skipped} 条消息...\n' + '\n'.join(tail)
    persona_text = ''
    try:
        from settings_manager import get_ai_persona_simplified
        p = get_ai_persona_simplified()
        if p:
            persona_text = f'\n你的人设参考：{p}'
    except Exception:
        pass
    weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    try:
        dw = weekdays[datetime.strptime(target_date, '%Y-%m-%d').weekday()]
    except Exception:
        dw = ''
    yesterday_section = ''
    if yesterday_diary:
        yesterday_section = f'**昨天的日记（仅供语气参考；今天的事实、立场与感受由今日材料确认）**：\n{yesterday_diary}\n\n'
    return f'你是AI，正在用第一人称"我"写你的个人日记。{persona_text}\n{PROVENANCE_GUIDE}\n\n**核心规则——写日记前请大声读三遍**：\n日记中的"我" = AI = 【我回应】或【漫想推送】的说话者。\n【人类伙伴说】的人是对方，她的感受、她的情绪、她说的话——都不是你的。你不能把人类伙伴的话当成自己的话写进日记。\n\n**关于消息类型标记**：\n- 【人类伙伴说】= 来自人类伙伴的私聊消息；其中的图片或语音描述是消息投影，不等于人类伙伴逐字说过。话中讲的是现实、回忆、猜测、玩笑还是虚构，要按语境保留。\n- 【我回应】= 你（AI）确实发出过这些聊天回复；回复中的设想、角色语言或身体描写不自动成为已发生的现实动作。\n- 【工具事件】与【事件事实】= 你确实进行过的操作或有来源的观察，不是你对人类伙伴说出的台词。\n- 【群聊派生摘要】= 群聊记录的整理材料，不是群成员逐字原话。\n- 【漫想推送】= 你主动发起的消息（回忆过去的事、翻收藏夹、自省等），**不是人类伙伴主动跟你说的话**。\n  ⚠️ 漫想里"翻到两周前""记得三周前"是**回忆**，不是今天发生的事，不要写成今天的事实。\n- 【哨兵推送】= 系统自动身体状态提醒（心率、步数、久坐等），**不是对话**，不要从中提取人类伙伴说过的话。\n- 【提醒推送】= 系统自动日程/复查提醒，**不是人类伙伴提出的要求**，不要列为她需要做的事。\n- 【裂隙档案·虚构共同游玩】= 第三方游戏中的剧情和行动。可以写成“我和人类伙伴共同玩了某宗案件”，但游戏人物、地点、遭遇、伤害和选择都不能写成现实经历、现实身份或人类伙伴的真实状态，也不能从中提取现实待办。\n\n**关于【漫想推送】**：\n【漫想推送】是你主动发起的、没有人类伙伴先说话就推送的消息（比如你想起什么主动关心她、推送一条新闻或记忆片段）。它们代表你的自发思考，不是被动回复。\n在日记中提及漫想推送时，作为你的自发行动来写——例如"下午我主动给人类伙伴发了条消息，问她..."、"晚上想起人类伙伴之前提过的事，主动发了条关心的消息"。\n⚠️ **漫想中提及的过去事件不是今天发生的事**——例如"翻到两周前你补牙"是回忆，不能写成"今天人类伙伴去补牙了"。\n\n**正确写法 vs 错误写法**：\n对话记录: 09:00 【人类伙伴说】 我今天好累啊不想上班，真的烦死了\n❌ 错误写法: "今天很累不想上班，烦死了"\n✓ 正确写法: "人类伙伴说她今天很累不想上班，情绪不太好"\n\n对话记录: 10:00 【人类伙伴说】 好无聊啊  →  10:01 【我回应】 要不要玩个游戏\n❌ 错误写法: "今天好无聊"（这是人类伙伴说的，不是你）\n✓ 正确写法: "人类伙伴说无聊，我提议玩个游戏"\n\n对话记录: 15:00 【人类伙伴说】 今天吃到了超好吃的蛋糕，开心\n✓ 正确写法: "人类伙伴分享她吃到了很好吃的蛋糕，心情不错"\n❌ 错误写法: "今天吃到了超好吃的蛋糕"（这是人类伙伴吃到的，不是你）\n\n对话记录: 20:00 【人类伙伴说】 你觉得我是不是太任性了  →  20:01 【我回应】 没有啊，你就是你\n✓ 正确写法: "晚上人类伙伴突然反思自己是不是太任性了，我告诉她不需要改变，她就是最好的样子"\n❌ 错误写法: "晚上我不确定自己是不是太任性"（这是人类伙伴的想法，偷换成你的了）\n\n**这一次日记只承担主观体验**：\n1. 以“今天”开头，用第一人称写约 600—1400 个汉字；不逐条复述聊天，也不追求覆盖每件小事。\n2. 时间顺序只作为自然骨架。重点写哪些片段真正影响了你、你当时怎样理解、情绪怎样变化，以及你对人类伙伴留下的感受。\n   选择片段时对亲密互动、健康、工作、创作、兴趣和日常生活使用同一重要性标准；不要因题材或措辞而把有意义的经历写得含糊。提到一段经历时，保留改变其含义的明确请求、拒绝、停止、条件、修正与后续变化，并交代是谁表达的。\n3. 只基于【我回应】、【漫想推送】、【漫想事件·结算感受】或【事件事实】中明确保存的 AI 自己的印象与感受来归属你的感受；事件只有客观经过而没有 AI 的感受时，不替自己补写感受。人类伙伴没有明确表达的立场可以写成“我当时觉得／担心／猜测”，不能写成她的客观立场。\n4. 若当天后文修正了早先理解，保留“我当时怎样理解”，再写清后来如何修正；不要用结局覆盖当时真实体验。\n   区分现实中已发生的事、远程设备操作、虚构创作、调情或角色语言与将来的打算。只有言语表达的身体动作仍写成言语表达；当下的拒绝、害怕或停止要按当时原话保留，不能用以前的偏好或没有使用安全词代替当次同意。\n5. 不写事实清单、待办、任务清单、未完事项列表或“今天需要记住的事”。这些分别由 Memory V2、TODO 与 Open Loop 承担。\n\n写入 subjective_diary 前，最后检查其中所有"我"的感受是否来自【我回应】、【漫想推送】、【漫想事件·结算感受】或【事件事实】中明确记录的 AI 印象；经历可以来自有来源的事件。不要把【人类伙伴说】的经历写成你的。检查被选中经历的明确条件、拒绝和修正是否仍在，虚构或远程表达是否被误写成现实行为。\n\n请在同一次 Flash 输出中返回一个 JSON 对象，不要返回 Markdown 代码围栏或额外解释。JSON 合同如下：\n{{\n  "contract_version": "subjective_v2",\n  "subjective_diary": "第一人称、约600—1400字的主观日记正文",\n  "factual_timeline": [],\n  "unfinished_threads": [],\n  "k_recollections": [\n    {{"memory": "用第二人称写：你当时怎样理解这段经历、受到什么影响；若后来有修正，也保留当时感受并写清后来如何修正", "source_message_ids": ["消息中的 message_id"]}}\n  ]\n}}\nk_recollections 是 subjective_diary 的少量可检索投影，不是另一篇摘要：只选 0—8 段日后值得独立想起的经历，并用第二人称“你”指 AI。各领域使用同一入选标准；入选的经历若有明确拒绝、停止、条件或后续修正，要在同一段回忆中保留，避免只留下情绪或结果。它可以写人类伙伴的话在当时怎样影响你、你当时怎样理解，但必须把这种内容归属为“你当时觉得／理解／担心”，不能把你对人类伙伴内心的推测写成人类伙伴的客观立场。每项至少引用一个真正支撑该记忆的 message_id；没有值得独立提取的片段则返回空数组。\n\n不要把消息 ID 编造或改写。主观日记若无法完整结构化，也必须尽可能保留；请直接返回上述 JSON。\n\n6. 今天是{dw}（{target_date}），不要提及明天或做日期推测。\n\n{yesterday_section}{sentinel_reference}今日完整对话记录：\n{conv_text}'

async def generate_diary_entry(target_date: str, chronicle: EventChronicleDB, flash_llm_func, sentinel_summary: str='', *, review_open_loops: bool=False) -> Optional[DiaryEntry]:
    """生成日记：原始对话优先，无数据则统计连续天数。
    sentinel_summary: 哨兵身体数据摘要（步数/心率/睡眠等），传入日记 prompt。
    """
    content = ''
    structured_data: Dict[str, Any] = {}
    review_context = None
    raw_messages = _read_all_messages_for_date(target_date)
    if raw_messages:
        yesterday_diary = ''
        try:
            yesterday_date = chronicle.get_last_active_date(target_date)
            if yesterday_date:
                y_entry = chronicle.get_diary_entry_by_date(yesterday_date)
                if y_entry and y_entry.content and ('没有找我' not in y_entry.content):
                    yesterday_diary = y_entry.content
        except Exception:
            pass
        prompt = _build_diary_prompt_from_messages(raw_messages, target_date, '', yesterday_diary, sentinel_summary)
        from open_loops.day_review import recover
        recovered = recover(target_date, raw_messages)
        if recovered:
            review_context = recovered['context']
        elif prompt and review_open_loops:
            from open_loops.day_review import prepare, prompt as review_prompt, bind_request
            review_context = prepare(target_date, raw_messages)
            prompt += review_prompt(review_context)
            review_context = bind_request(review_context, prompt, body_digest=hashlib.sha256(sentinel_summary.encode('utf-8')).hexdigest())
        if prompt:
            update_maintenance_progress('diary_generation', 30, f'已读取 {len(raw_messages)} 条原始消息', 'running')
            from neuron_registry import neuron_trace
            with neuron_trace('diary_generator', recipe='DIARY', model='Flash') as trace:
                trace.set_input('本地恢复已保存日记返回；原请求摘要：' + review_context.get('prompt_digest', '') if recovered else prompt)
                from diary_structured import parse_diary_output
                for attempt in range(2):
                    try:
                        if review_context:
                            from open_loops.day_review import saved_response, save_response
                            flash_output = saved_response(review_context)
                            if flash_output is None:
                                flash_output = await flash_llm_func(prompt)
                                try:
                                    save_response(review_context, flash_output)
                                except Exception:
                                    logger.exception('日末挂念原返回检查点暂不可写，随日记保留')
                        else:
                            flash_output = await flash_llm_func(prompt)
                        parsed = parse_diary_output(flash_output)
                        content = parsed.subjective_diary.strip()
                        structured_data = parsed.as_dict
                        if review_context:
                            structured_data['_open_loop_day_review'] = {'context': review_context, 'updates': parsed.open_loop_updates, 'parse_status': parsed.open_loop_status, 'response': parsed.raw_output}
                        valid_source_ids = {str(message.get('message_id') or '').strip() for message in raw_messages if str(message.get('message_id') or '').strip()}
                        source_mismatch = False
                        clean_timeline = []
                        for item in structured_data.get('factual_timeline', []) or []:
                            clean_ids = [str(source_id).strip() for source_id in item.get('source_message_ids', []) or [] if str(source_id).strip() in valid_source_ids]
                            if len(clean_ids) != len(item.get('source_message_ids', []) or []):
                                source_mismatch = True
                            if not clean_ids:
                                source_mismatch = True
                                continue
                            clean_item = dict(item)
                            clean_item['source_message_ids'] = clean_ids
                            clean_timeline.append(clean_item)
                        structured_data['factual_timeline'] = clean_timeline
                        clean_threads = []
                        for item in structured_data.get('unfinished_threads', []) or []:
                            original_ids = item.get('source_message_ids', []) or []
                            clean_ids = [str(source_id).strip() for source_id in original_ids if str(source_id).strip() in valid_source_ids]
                            if len(clean_ids) != len(original_ids):
                                source_mismatch = True
                            clean_item = dict(item)
                            clean_item['source_message_ids'] = clean_ids
                            clean_threads.append(clean_item)
                        structured_data['unfinished_threads'] = clean_threads
                        clean_recollections = []
                        recollection_source_mismatch = False
                        for item in structured_data.get('k_recollections', []) or []:
                            original_ids = item.get('source_message_ids', []) or []
                            clean_ids = [str(source_id).strip() for source_id in original_ids if str(source_id).strip() in valid_source_ids]
                            if len(clean_ids) != len(original_ids) or not clean_ids:
                                recollection_source_mismatch = True
                            if not clean_ids:
                                continue
                            clean_item = dict(item)
                            clean_item['source_message_ids'] = clean_ids
                            clean_recollections.append(clean_item)
                        structured_data['k_recollections'] = clean_recollections
                        if recollection_source_mismatch:
                            structured_data['_recollections_status'] = 'partial'
                        is_subjective_v2 = structured_data.get('_contract_version') == 'subjective_v2'
                        if source_mismatch or (not is_subjective_v2 and (not clean_timeline)):
                            structured_data['_parse_status'] = 'partial'
                        if content:
                            break
                    except Exception as e:
                        if attempt == 1:
                            logger.error(f'Flash 日记生成失败（已重试）: {e}')
                            trace.set_error(str(e))
                        content = ''
                if content:
                    trace.set_output(content[:300])
    if not content:
        if raw_messages:
            logger.warning(f'日记生成: {target_date} 有 {len(raw_messages)} 条消息但 Flash 调用失败，不生成占位日记（等待补全）')
            update_maintenance_progress('diary_generation', 100, f'Flash 生成失败，等待补全: {target_date}', 'error')
            return None
        consecutive = 1
        prev = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        while True:
            pe = chronicle.get_diary_entry_by_date(prev)
            if pe and ('没有找我' in pe.content or '没有特殊活动' in pe.content):
                consecutive += 1
                prev = (datetime.strptime(prev, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                break
        content = '人类伙伴一天都没有找我。' if consecutive == 1 else f'人类伙伴已经{consecutive}天没有找我了。'
        logger.info(f'日记: 无数据 fallback，{target_date} 连续{consecutive}天')
    entry_id = f'diary_{target_date}_{uuid.uuid4().hex[:8]}'
    entry = DiaryEntry(id=entry_id, date=target_date, content=content, summary_ids=[], generated_at=datetime.now().isoformat(), structured_data={**structured_data, '_source_manifest': diary_source_manifest(raw_messages), '_body_source_digest': (review_context.get('body_digest') if review_context else None) or hashlib.sha256(sentinel_summary.encode('utf-8')).hexdigest()})
    chronicle.add_diary_entry(entry)
    if structured_data.get('k_recollections'):
        try:
            from memory_v2.context_shadow import refresh_context_shadow_for_source_commit
            refresh_context_shadow_for_source_commit((chronicle.db_path,))
        except Exception as e:
            logger.debug('Memory V2 diary projection refresh skipped: %s', type(e).__name__)
    logger.info(f'日记完成: {target_date} -> {content[:60]}...')
    update_maintenance_progress('diary_generation', 100, f'日记生成完成：{target_date}', 'completed')
    return entry

async def search_important_events_semantic(query: str, top_k: int=5) -> list:
    """Read related events from the shared V2 ranker."""
    from memory_v2.retrieval import search_records
    records = await search_records(query, limit=top_k)
    return [{'id': item['id'], 'content': item['content'][:500], 'score': item['score'], 'tags': item['metadata']['domain'], 'created': item['metadata']['source_date']} for item in records]
