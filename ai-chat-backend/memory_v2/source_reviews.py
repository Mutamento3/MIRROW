"""Optional, source-version-bound human reviews of immutable event read views.

No encoder, authority migration or runtime writer. Removing the local review
file restores the stored event. Raw settlement quotes need their own proof;
they never replace an encoding batch's historical projected source bytes.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

VERSION = 1
FILENAME = 'memory_v2_source_reviews.json'
RAW_FIELDS = ('id', 'message_id', 'session_id', 'timestamp', 'role', 'content',
              'active_date', 'calendar_date', 'event_type', 'tool_calls',
              'is_wander', 'is_sentinel', 'is_reminder')
EVENT_FIELDS = ('id', 'active_date', 'subject_id', 'summary', 'attributes_json', 'content_digest')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def raw_revision(row):
    return _digest({key: row.get(key) for key in RAW_FIELDS})


def event_revision(row):
    return _digest({key: row.get(key) for key in EVENT_FIELDS})


def read_raw_rows(path, ids):
    """Only quote-bearing row fields; reasoning, images and credentials excluded."""
    ids = tuple(dict.fromkeys(ids))
    if not ids:
        return {}
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        columns = {row[1] for row in db.execute('PRAGMA table_info(conversation_messages)')}
        fields = [key for key in RAW_FIELDS if key in columns]
        result = {}
        for offset in range(0, len(ids), 200):
            batch = ids[offset:offset + 200]
            marks = ','.join('?' for _ in batch)
            for row in db.execute(f"SELECT {','.join(fields)} FROM conversation_messages "
                                  f"WHERE message_id IN ({marks})", batch):
                result[row['message_id']] = dict(row)
        return result


def reviewed_messages(source, document):
    """Verify every review proof, then expose only explicitly approved raw quotes."""
    proofs = document.reviewed_source_versions
    if not proofs:
        return {}
    rows = read_raw_rows(source.db_path, [item[0] for item in proofs])
    if any(mid not in rows or raw_revision(rows[mid]) != digest for mid, digest, _ in proofs):
        raise ValueError('review_source_changed')
    projected = {m.message_id: m for m in source.read_message_ids(tuple(rows))}
    result = {}
    for mid, _digest_value, quote_raw in proofs:
        if mid not in projected:
            raise ValueError('review_source_unavailable')
        row = rows[mid]
        m = projected[mid]
        if (m.row_id != row['id'] or m.session_id != row['session_id']
                or m.timestamp != row['timestamp'] or m.role != row['role']):
            raise ValueError('review_source_identity_changed')
        if quote_raw:
            if row['role'] != 'event' or row.get('event_type') != 'wander_activity':
                raise ValueError('review_quote_kind_invalid')
            result[mid] = replace(m, content=str(row['content']))
    return result


class EventSourceReviews:
    def __init__(self, authority_path, *, path=None):
        self.authority_path = Path(authority_path).resolve()
        self.path = Path(path) if path is not None else self.authority_path.parent / FILENAME
        self._file_digest = ''

    def _read(self):
        if not self.path.is_file():
            self._file_digest = ''
            return []
        if self.path.is_symlink() or self.path.stat().st_size > 4_000_000:
            raise ValueError('review_file_invalid')
        raw = self.path.read_bytes()
        self._file_digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != VERSION
                or data.get('enabled') is not True or not isinstance(data.get('entries'), list)
                or len(data['entries']) > 500):
            raise ValueError('review_contract_invalid')
        return data['entries']

    def prepare(self, event_rows, source_map):
        """Invalid/missing optional reviews fall back to original event records."""
        try:
            entries = self._read()
            if not entries:
                return {}
            if any(not isinstance(e, dict) or not isinstance(e.get('event_id'), str)
                   or not isinstance(e.get('sources'), list)
                   or any(not isinstance(p, dict) or not isinstance(p.get('message_id'), str)
                          for p in e['sources']) for e in entries):
                return {}
            events = {row['id']: dict(row) for row in event_rows}
            ids = [p['message_id'] for e in entries for p in e.get('sources', [])]
            raw_rows = read_raw_rows(self.authority_path, ids)
            from .conversation_source import ConversationSource, digest_text
            projected = {m.message_id: m for m in ConversationSource(self.authority_path).read_message_ids(
                tuple(dict.fromkeys(ids)))}
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            return {}
        result, seen = {}, set()
        for entry in entries:
            eid = entry.get('event_id')
            if eid in seen:
                result.pop(eid, None)
                continue
            seen.add(eid)
            try:
                event = events[eid]
                if entry.get('event_revision') != event_revision(event):
                    continue
                summary = entry['summary']
                if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 4000:
                    continue
                proofs = entry['sources']
                original_ids = {row['message_id'] for row in source_map.get(eid, [])}
                if (not isinstance(proofs, list) or not proofs
                        or len(proofs) != len(original_ids)
                        or len({p['message_id'] for p in proofs}) != len(proofs)
                        or {p['message_id'] for p in proofs} != original_ids):
                    continue
                if any(p['message_id'] not in raw_rows or
                       p['raw_revision'] != raw_revision(raw_rows[p['message_id']]) for p in proofs):
                    continue
                if any(s['message_id'] not in projected or
                       projected[s['message_id']].row_id != s['message_row_id'] or
                       projected[s['message_id']].session_id != s['session_id'] or
                       projected[s['message_id']].timestamp != s['source_ts'] or
                       projected[s['message_id']].role != s['source_role'] or
                       digest_text(projected[s['message_id']].content) != s['encoded_digest']
                       for s in source_map[eid]):
                    continue
                versions = tuple((p['message_id'], p['raw_revision'], p.get('quote_raw') is True)
                                 for p in proofs)
                if any(quote and (raw_rows[mid]['role'] != 'event' or
                       raw_rows[mid].get('event_type') != 'wander_activity')
                       for mid, _, quote in versions):
                    continue
                attributes = json.loads(event['attributes_json'] or '{}')
                from .recurrence import validated_label
                label = validated_label(entry.get('recurrence_label'), entry.get('recurrence_confidence'))
                if label:
                    attributes.update(recurrence_label=label,
                                      recurrence_confidence=entry['recurrence_confidence'])
                # Immutable completed analysis/listening receipts contribute
                # occurrences. The review itself cannot invent completion/counts.
                music = []
                for mid in original_ids:
                    row = raw_rows[mid]
                    if row.get('event_type') != 'wander_activity':
                        continue
                    calls = json.loads(row.get('tool_calls') or '[]')
                    for call in calls:
                        overview = (call.get('extra_data') or {}).get('execution_overview') or {}
                        for node in overview.get('music_materials', []):
                            if ((node.get('completion_signal') == 'analysis_complete' or (
                                    node.get('completion_signal') == 'listening_finished' and
                                    (node.get('music_execution') or {}).get('experience_kind') == 'listening_completed'))
                                    and node.get('node_id') and node.get('song_id') and overview.get('activity_id')):
                                music.append({'subject_id': 'k', 'pattern_key': 'music:' + str(node['song_id']),
                                    'occurrence_key': str(overview['activity_id']) + ':' + str(node['node_id']),
                                    'discussion_key': eid, 'source_message_id': mid,
                                    'source_date': row['active_date'], 'temporal_status': 'occurred'})
                if music:
                    attributes['recorded_music'] = music
                result[eid] = {'summary': summary.strip(), 'attributes': attributes, 'sources': versions}
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return result

    def current(self, documents):
        """Cached projections expire if a review is revoked or any raw row changes."""
        proofs = {mid: digest for doc in documents for mid, digest, _ in doc.reviewed_source_versions}
        try:
            if not self.path.is_file():
                return not self._file_digest and not proofs
            if (self.path.is_symlink() or self.path.stat().st_size > 4_000_000
                    or hashlib.sha256(self.path.read_bytes()).hexdigest() != self._file_digest):
                return False
            if not proofs:
                return True
            rows = read_raw_rows(self.authority_path, tuple(proofs))
            return all(mid in rows and raw_revision(rows[mid]) == digest for mid, digest in proofs.items())
        except (OSError, ValueError, sqlite3.Error):
            return False
