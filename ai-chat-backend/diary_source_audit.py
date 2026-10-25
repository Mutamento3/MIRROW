"""Retrospective source inventory. Never asserts historical model consumption."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


class DiaryAuditConflict(RuntimeError):
    pass


def text_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def source_snapshot(date):
    from event_chronicle import _read_all_messages_for_date, diary_source_manifest
    from silicon_perception.analysis.daily_summary import sentinel_diary_section
    messages = _read_all_messages_for_date(date)
    return {'messages': diary_source_manifest(messages),
            'body_digest': text_digest(sentinel_diary_section(date) or '')}


def _review_content_matches(entry):
    saved = (entry.structured_data or {}).get('_source_review') or {}
    return (saved.get('version') == 1 and saved.get('mode') == 'content_review'
            and saved.get('diary_id') == getattr(entry, 'id', None)
            and saved.get('diary_content_digest') == text_digest(entry.content)
            and saved.get('recollections_digest') == text_digest(json.dumps(
                (entry.structured_data or {}).get('k_recollections', []), ensure_ascii=False, sort_keys=True)))


def review_matches(entry, observed):
    """A content review approves this exact prose/source pair, not generation."""
    saved = (entry.structured_data or {}).get('_source_review') or {}
    return _review_content_matches(entry) and saved.get('observed') == observed


def review_messages_match(entry, messages):
    """Approve reviewed chat/prose while a consumer separately reads fresh body facts.

    This narrower check never declares the complete reviewed snapshot current.
    Consumers must carry the actual body snapshot into their commit guard.
    """
    saved = (entry.structured_data or {}).get('_source_review') or {}
    reviewed_messages = (saved.get('observed') or {}).get('messages')
    return (_review_content_matches(entry) and isinstance(reviewed_messages, dict)
            and reviewed_messages.get('version') == 1 and reviewed_messages == messages)


def review(chronicle, date, *, expected_content_digest, expected_snapshot,
           findings, corrections=(), recollection_corrections=()):
    """Save an explicitly verified review with optional anchored local edits.

    Callers back up first and verify the findings against their cited messages.
    Generation receipts retain their original provenance. Recollection edits
    require the same explicit anchors and exact original text as prose edits.
    """
    with closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn:
        row = conn.execute('SELECT id,content,structured_json FROM diary_entries WHERE date=?', (date,)).fetchone()
    if row is None:
        raise LookupError('该日期没有日记')
    entry_id, content, original = row
    if text_digest(content) != expected_content_digest or source_snapshot(date) != expected_snapshot:
        raise DiaryAuditConflict('复核期间日记或原料已变化')
    from event_chronicle import _read_all_messages_for_date
    source_ids = {str(item.get('message_id') or '') for item in _read_all_messages_for_date(date)}
    for item in [*findings, *corrections, *recollection_corrections]:
        anchors = item.get('source_message_ids') or []
        if not isinstance(anchors, list) or not anchors or any(anchor not in source_ids for anchor in anchors):
            raise ValueError('复核结论需要有效的当日消息锚点')
    revised = content
    for item in corrections:
        old, new = item.get('before'), item.get('after')
        if not isinstance(old, str) or not old or not isinstance(new, str) or revised.count(old) != 1:
            raise DiaryAuditConflict('更正原句缺失或不唯一')
        revised = revised.replace(old, new, 1)
    metadata = json.loads(original or '{}')
    recollections = metadata.get('k_recollections') or []
    for item in recollection_corrections:
        old, new = item.get('before'), item.get('after')
        matches = [memory for memory in recollections if isinstance(memory, dict)
                   and isinstance(old, str) and old and old in str(memory.get('memory') or '')]
        if len(matches) != 1 or not isinstance(new, str) or matches[0]['memory'].count(old) != 1:
            raise DiaryAuditConflict('伴生回忆更正原句缺失或不唯一')
        memory = matches[0]
        memory['memory'] = memory['memory'].replace(old, new, 1)
        memory['source_message_ids'] = list(dict.fromkeys([
            *memory.get('source_message_ids', []), *item['source_message_ids']]))
    # The duplicate display field follows the prose; generated source receipts
    # and independent autobiographical projections are never re-stamped.
    if 'subjective_diary' in metadata:
        metadata['subjective_diary'] = revised
    value = {'version': 1, 'mode': 'content_review', 'diary_id': entry_id,
             'diary_content_digest': text_digest(revised), 'observed': expected_snapshot,
             'recollections_digest': text_digest(json.dumps(metadata.get('k_recollections', []), ensure_ascii=False, sort_keys=True)),
             'generation_receipts': 'preserved', 'reviewed_by': 'codex',
             'reviewed_at': datetime.now(timezone.utc).isoformat(),
             'findings': findings, 'corrections': list(corrections),
             'recollection_corrections': list(recollection_corrections),
             'previous_content_digest': expected_content_digest}
    metadata['_source_review'] = value
    if source_snapshot(date) != expected_snapshot:
        raise DiaryAuditConflict('提交前原料已变化')
    with closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn, conn:
        result = conn.execute('UPDATE diary_entries SET content=?,structured_json=? WHERE date=? AND id=? AND content=? AND structured_json IS ?',
            (revised, json.dumps(metadata, ensure_ascii=False), date, entry_id, content, original))
        if result.rowcount != 1:
            raise DiaryAuditConflict('提交前日记已变化')
    return value


def audit(chronicle, date):
    # Read the exact row first, then compare-and-update only its metadata. This
    # preserves IDs, dates, prose, summaries, and existing AI recollections.
    with closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn:
        row = conn.execute('SELECT id,content,structured_json FROM diary_entries WHERE date=?', (date,)).fetchone()
    if row is None:
        raise LookupError('该日期没有日记')
    entry_id, content, original = row
    metadata = json.loads(original or '{}')
    observed = source_snapshot(date)  # read failures leave the old audit untouched
    value = {'version': 1, 'mode': 'retrospective', 'diary_id': entry_id,
             'diary_content_digest': text_digest(content), 'observed': observed,
             'generation_consumption': 'unknown'}
    previous = metadata.get('_source_audit', {})
    if {k: v for k, v in previous.items() if k != 'checked_at'} == value:
        return previous
    value['checked_at'] = datetime.now(timezone.utc).isoformat()
    metadata['_source_audit'] = value
    with closing(sqlite3.connect(chronicle.db_path, timeout=10)) as conn, conn:
        result = conn.execute('UPDATE diary_entries SET structured_json=? WHERE date=? AND id=? AND content=? AND structured_json IS ?',
            (json.dumps(metadata, ensure_ascii=False), date, entry_id, content, original))
        if result.rowcount != 1:
            raise DiaryAuditConflict('核查期间日记已变化，请重试')
    return value


def status(chronicle, date):
    entry = chronicle.get_diary_entry_by_date(date)
    if entry is None:
        raise LookupError('该日期没有日记')
    current = source_snapshot(date)
    metadata = entry.structured_data or {}
    manifest = metadata.get('_source_manifest')
    body = metadata.get('_body_source_digest')
    saved = metadata.get('_source_audit') or {}
    def compare(old, new):
        return 'unknown' if old is None else ('current' if old == new else 'changed')
    unchanged = (saved.get('observed') == current and saved.get('diary_id') == entry.id
                 and saved.get('diary_content_digest') == text_digest(entry.content))
    return {'generation': {
        'messages': compare(manifest if isinstance(manifest, dict) and manifest.get('version') == 1 else None, current['messages']),
        'body': compare(body, current['body_digest'])},
        'retrospective': ('unchanged_since_check' if unchanged else 'changed_since_check') if saved else 'not_checked',
        'content_review': ('current' if review_matches(entry, current) else 'changed_since_review')
            if metadata.get('_source_review') else 'not_reviewed',
        'current_message_count': current['messages']['count'], 'checked_at': saved.get('checked_at')}
