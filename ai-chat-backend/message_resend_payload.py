"""Carry the accepted user row's frozen attachments into a resend generation.

The browser sends an accepted identity, not another upload or a live share ID.
Both chat transports resolve the same stored payload without re-running shares.
"""
import base64
import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
ATTACHMENT_COLUMNS = ('images', 'voice_attachment', 'long_text_card', 'tool_calls', 'tool_summary', 'event_type')
_JSON_FIELDS = {'images': ('images', list), 'voice_attachment': ('voiceAttachment', dict), 'long_text_card': ('longTextCard', dict), 'tool_calls': ('toolCalls', list)}

def message_from_row(row):
    row = dict(row)
    message = {'id': row['message_id'], 'role': 'user', 'content': row['content'], 'timestamp': row['timestamp']}
    for column, (key, expected) in _JSON_FIELDS.items():
        raw = row.get(column)
        if raw:
            try:
                value = json.loads(raw) if isinstance(raw, str) else copy.deepcopy(raw)
                if value is None:
                    continue
                if not isinstance(value, expected):
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise ValueError('原消息附件格式不完整，编辑稿已保留，请先核对附件') from exc
            message[key] = value
    if row.get('event_type'):
        message['eventType'] = row['event_type']
    return message

def replacement_attachments(row, content):
    """Copy frozen metadata, changing only the actual composer text."""
    row = dict(row)
    message = message_from_row(row)
    values = {column: row.get(column) for column in ATTACHMENT_COLUMNS}
    return values

def _local_image_path(image, root):
    from wander_manager.chat_attachment_boundary import sanitize_chat_image_metadata
    directory = Path(root).resolve()
    metadata = sanitize_chat_image_metadata(image)
    if not metadata:
        raise ValueError('原图片地址不可用，请核对附件')
    path = (directory / metadata['url'].rsplit('/', 1)[-1]).resolve()
    if path.parent != directory or not path.is_file():
        raise ValueError('原图片文件不存在，消息及附件记录已保留，请核对原图')
    return (path, metadata)

def validate_message_files(message, image_root):
    """Fail before deleting history if a retained local attachment is missing."""
    paths = []
    for image in message.get('images') or []:
        if str(image.get('url') or '').startswith('data:image/'):
            continue
        paths.append(_local_image_path(image, image_root)[0])
    try:
        for path in paths:
            with path.open('rb') as stream:
                if not stream.read(1):
                    raise ValueError('原图片文件为空，编辑稿已保留，请核对附件')
    except OSError as exc:
        raise ValueError('原图片暂时无法读取，编辑稿已保留，请先核对附件') from exc

def _image_data_urls(images, root):
    urls = []
    for image in images or []:
        url = str(image.get('url') or '')
        if url.startswith('data:image/') and ';base64,' in url:
            try:
                base64.b64decode(url.split(';base64,', 1)[1], validate=True)
            except ValueError as exc:
                raise ValueError('原图片数据损坏，请核对附件') from exc
            urls.append(url)
            continue
        path, metadata = _local_image_path(image, root)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError('原图片暂时无法读取，请稍后重试') from exc
        if not raw:
            raise ValueError('原图片文件为空，请核对附件')
        mime = metadata['type'] if metadata['type'].startswith('image/') else 'image/png'
        urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}")
    return urls

def load_accepted_resend(session_id, message_id, content, image_root, *, chronicle=None):
    """Read the confirmed row and local media; never fetch arbitrary URLs."""
    if chronicle is None:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
    with chronicle._lock, closing(sqlite3.connect(chronicle.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_resends'").fetchone():
            raise ValueError('重发尚未确认，请先确认保存结果')
        receipt = conn.execute('SELECT reconciled FROM conversation_resends WHERE session_id=? AND replacement_id=?', (session_id, message_id)).fetchone()
        row = conn.execute('SELECT * FROM conversation_messages WHERE session_id=? AND message_id=? AND role=?', (session_id, message_id, 'user')).fetchone()
        if not receipt or not receipt['reconciled'] or (not row) or (row['content'] != content):
            raise ValueError('重发消息尚未确认或已变化，请核对聊天记录')
        saved = message_from_row(row)
    text = saved['content']
    images = saved.get('images') or []
    if images:
        from image_message_content import image_caption
        text = image_caption(text)
    share = None
    share_images = []
    return {'message': text, 'content': saved['content'], 'images': images, 'image_data_urls': _image_data_urls(images, image_root), 'voice_attachment': saved.get('voiceAttachment'), 'long_text_card': saved.get('longTextCard')}
