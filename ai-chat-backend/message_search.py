"""Read-only, cursor-paged search over persisted chat rows and visible attachments.

SQLite applies session/cursor ordering and LIMIT. The deterministic predicate
projects one candidate at a time; no full session, JSON cache or model is loaded.
"""
import json
import sqlite3
from contextlib import closing
from typing import Literal, get_args
MessageSearchType = Literal['all', 'text', 'image', 'voice', 'card', 'event', 'tool']
TYPE_LABELS = {'text': '纯文字', 'image': '图片', 'voice': '语音', 'card': '小卡片', 'event': '事件／通知', 'tool': '工具记录'}
COLUMNS = ('role', 'content', 'event_type', 'images', 'voice_attachment', 'long_text_card', 'tool_calls')
CARD_FIELDS = ('title', 'body', 'artist', 'track_preview', 'report_summary', 'summary', 'text', 'description', 'name', 'category', 'comment', 'reason', 'brief', 'plan', 'completion_label', 'k_impression', 'first_impression', 'favorite', 'concern', 'suggestion', 'occasion', 'visual_description', 'lyrics_excerpt', 'melody_summary')
CARD_CONTAINERS = ('items', 'review', 'intent', 'evidence', 'artifacts')
EVENT_FIELDS = {'workshop': ('title', 'summary', 'reason', 'plan', 'brief')}
SOCIAL_TOOLS = {'manage_social_feed', '💌 打开了朋友圈', '💭 逛朋友圈', '逛朋友圈', '打开了朋友圈', '打开了共域'}

def _json(value, kind):
    if isinstance(value, kind):
        return value
    if not value:
        return kind()
    try:
        parsed = json.loads(value or 'null')
        return parsed if isinstance(parsed, kind) else kind()
    except (ValueError, TypeError, RecursionError):
        return kind()

def _card_text(value, depth=0):
    if depth > 6:
        return
    if isinstance(value, list):
        for item in value:
            yield from _card_text(item, depth + 1)
    elif isinstance(value, dict):
        for key in CARD_FIELDS:
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                yield text
        for key in CARD_CONTAINERS:
            yield from _card_text(value.get(key), depth + 1)

def project_message(role, content, event_type, images, voice_attachment, long_text_card, tool_calls):
    types = set()
    sections = []

    def add(source, value):
        if isinstance(value, str) and value.strip():
            sections.append((source, value))
    if role in ('event', 'notification') or event_type:
        types.add('event')
    pictures = _json(images, list)
    if pictures:
        types.add('image')
        for picture in pictures:
            if isinstance(picture, dict):
                add('图片名称', picture.get('filename'))
    voice = _json(voice_attachment, dict)
    if voice:
        types.add('voice')
        add('语音文字', voice.get('text'))
    card = _json(long_text_card, dict)
    if card:
        types.add('card')
        for text in _card_text(card):
            add('卡片内容', text)
    for call in _json(tool_calls, list):
        if not isinstance(call, dict):
            continue
        tool = call.get('tool') or call.get('name')
        if not tool:
            continue
        extra = call.get('extra_data') if isinstance(call.get('extra_data'), dict) else {}
        specialized = tool in ('send_voice', 'ambient_event_link', 'band_touch')
        if tool == 'send_voice':
            types.add('voice')
            add('语音文字', extra.get('voice_text'))
        if extra.get('music_card') or extra.get('xhs_post_card') or extra.get('attachment_kind') == 'music':
            specialized = True
            types.add('card')
            for payload in ('music_card', 'xhs_post_card'):
                for text in _card_text(extra.get(payload)):
                    add('卡片内容', text)
            add('卡片附言', extra.get('user_text'))
        if extra.get('xhs_comment') is True:
            specialized = True
            types.add('card')
        for payload, fields in EVENT_FIELDS.items():
            if isinstance(extra.get(payload), dict):
                specialized = True
                types.add('event')
                for field in fields:
                    add('事件内容', extra[payload].get(field))
                source_key = {}.get(payload)
                sources = extra[payload].get(source_key, []) if source_key else []
                for source in sources if isinstance(sources, list) else []:
                    if isinstance(source, dict):
                        for field in ('content', 'sender_display_name', 'entity_name', 'speaker'):
                            add('事件来源', source.get(field))
                if payload == 'workshop':
                    for text in _card_text(extra[payload].get('artifacts')):
                        add('作品内容', text)
        if tool in SOCIAL_TOOLS:
            types.add('tool')
            add('工具记录', '打开了共域')
        elif not specialized and (role == 'assistant' or event_type in ('wander_activity', 'tool_event')):
            types.add('tool')
            add('工具记录', call.get('description'))
            if tool != 'eyes' and (not extra.get('memory_experience')):
                add('工具结果', call.get('result'))
    add('正文', content)
    if not types:
        types.add('text')
    return ([kind for kind in TYPE_LABELS if kind in types], sections)

def _snippet(sections, query, types):
    selected = next(((label, text) for label, text in sections if query and query in text.lower()), None)
    label, text = selected or (sections[0] if sections else ('消息类型', ' · '.join((TYPE_LABELS[kind] for kind in types))))
    hit = text.lower().find(query) if query else 0
    start = max(0, hit - 35)
    end = min(len(text), start + max(120, len(query) + 70))
    return (('…' if start else '') + text[start:end].replace('\n', ' ').replace('\r', ' ') + ('…' if end < len(text) else ''), label)

def search_messages(db_path, session_id, query='', limit=50, before_ts='', before_row_id=0, message_type='all'):
    if message_type not in get_args(MessageSearchType):
        raise ValueError('不支持的消息类型')
    query = query.strip().lower()
    if not query and message_type == 'all':
        raise ValueError('请输入关键词或选择消息类型')
    limit = max(1, min(int(limit), 50))

    def matches(*values):
        role, content, event_type = values[:3]
        if message_type == 'all' and query and (query in (content or '').lower()):
            return 1
        if message_type == 'event' and (not query) and (role in ('event', 'notification') or event_type):
            return 1
        types, sections = project_message(*values)
        return int((message_type == 'all' or message_type in types) and (not query or any((query in text.lower() for _, text in sections))))
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.create_function('chat_search_match', len(COLUMNS), matches, deterministic=True)
        where = "session_id=? AND role IN ('user','assistant','event','notification','system','parallel_summary') AND COALESCE(message_id,'')<>''"
        params = [session_id]
        if before_ts and before_row_id > 0:
            where += ' AND (timestamp < ? OR (timestamp = ? AND id < ?))'
            params.extend([before_ts, before_ts, before_row_id])
        columns = ','.join(COLUMNS)
        rows = conn.execute(f'SELECT id,message_id,timestamp,{columns} FROM conversation_messages WHERE {where} AND chat_search_match({columns}) ORDER BY timestamp DESC,id DESC LIMIT ?', (*params, limit + 1)).fetchall()
    has_more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        types, sections = project_message(*(row[column] for column in COLUMNS))
        snippet, source = _snippet(sections, query, types)
        items.append({'id': row['message_id'], 'role': row['role'], 'timestamp': row['timestamp'], 'types': types, 'snippet': snippet, 'snippet_source': source})
    last = rows[limit - 1] if has_more else None
    return {'items': items, 'has_more': has_more, 'next_cursor': {'timestamp': last['timestamp'], 'row_id': last['id']} if last else None}
