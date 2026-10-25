"""Resolve existing book provenance against current raw chat, never book prose.

Linked sources supplement a specific review target. They do not become radar
observations, occurrence counts, or evidence for neighboring review requests.
"""
import sqlite3
import hashlib
import json

from . import books

START_DATE = '2026-10-18'


def collect(bound, snapshot, session_id, source_date, *, source=None, locator=None, context_anchor_ids=None):
    context_gap = None
    ids, non_chat, missing_targets = [], set(), 0
    for target in bound:
        entry = next((e for e in snapshot.get(target['domain'], [])
                      if e['entry_id'] == target['entry_id']
                      and e['revision'] == target['revision']), None)
        missing_targets += entry is None
        for link in (entry or {}).get('maintenance_links', []):
            if not isinstance(link, dict):
                continue
            values = link.get('evidence_ids', [])
            if isinstance(values, list):
                ids.extend(v for v in values if isinstance(v, str) and v)
            metadata = link.get('evidence_sources', [])
            for ref in metadata if isinstance(metadata, list) else []:
                if not isinstance(ref, dict) or not isinstance(ref.get('message_id'), str):
                    continue
                ids.append(ref['message_id'])
                if ref.get('source_kind') not in {None, '', 'private_chat'}:
                    non_chat.add(ref['message_id'])
    ids = list(dict.fromkeys(ids))
    if locator is not None:
        ids = locator['linked_source_ids']
        non_chat = set(locator['non_chat_source_ids'])
        missing_targets = 0
        if 'context' in locator:
            from .habit_context_sources import VERSION
            if locator['context']['version'] != VERSION:
                raise ValueError('habit_context_version_unknown')
            context_anchor_ids = locator['context']['anchor_ids']
            context_gap = locator['context']['gap_minutes']
    coverage = {'linked_ids': len(ids), 'resolved_sources': 0, 'unresolved_or_ineligible': len(ids),
                'missing_targets': missing_targets,
                'linked_source_ids': ids, 'non_chat_source_ids': sorted(non_chat),
                'scope': 'current_raw_book_links_not_occurrence_counts'}
    if context_anchor_ids is not None:
        from .habit_context_sources import VERSION
        from scene_manager.scene import configured_gap_minutes
        context_gap = configured_gap_minutes() if context_gap is None else context_gap
        context_anchor_ids = [mid for mid in dict.fromkeys([*ids, *context_anchor_ids]) if mid not in non_chat]
        coverage['context'] = {'version': VERSION, 'gap_minutes': context_gap,
            'anchor_ids': context_anchor_ids, 'lookup_status': 'unavailable'}
    if not ids and context_anchor_ids is None:
        return [], coverage
    try:
        from memory_v2.conversation_source import ConversationSource, ConversationSourceError
        if source is None:
            path = books.ROOT / 'events/event_chronicle.db'
            if not path.is_file():
                return [], coverage
            source = ConversationSource(path, canonical_sessions_only=True)
        current = source.read_message_ids(tuple(ids))
    except (OSError, sqlite3.Error, ConversationSourceError):
        coverage['lookup_status'] = 'unavailable'
        return [], coverage
    rows = []
    for row in current:
        if (row.message_id in non_chat or row.session_id != session_id or row.role not in {'user', 'assistant'}
                or row.source_kind != 'chat' or row.event_type or not row.active_date
                or row.active_date >= source_date or not row.content.strip()):
            continue
        rows.append({'id': row.message_id, 'speaker': 'human' if row.role == 'user' else 'k',
                     'text': row.content, 'timestamp': row.timestamp, 'active_date': row.active_date,
                     'source_kind': 'private_chat', 'identity_basis': 'validated_canonical_chat'})
    rows.sort(key=lambda r: (r['timestamp'], r['id']))
    coverage.update(resolved_sources=len(rows), unresolved_or_ineligible=len(ids)-len(rows))
    coverage['identity_proof'] = hashlib.sha256(json.dumps(
        {'session_id': session_id, 'sources': rows}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if context_anchor_ids is not None:
        from .habit_context_sources import collect as context_sources
        context, coverage['context'] = context_sources(source,
            context_anchor_ids, session_id, source_date, gap_minutes=context_gap)
        known = {row['id'] for row in rows}
        rows.extend(row for row in context if row['id'] not in known)
        rows.sort(key=lambda r: (r['timestamp'], r['id']))
    return rows, coverage


def check_current(requests):
    selected = [r for r in requests if r.get('source_coverage') is not None]
    if not selected:
        return
    for request in selected:
        # The book may have just received a separately verified update. Source
        # lifecycle checks bind the frozen locators, not its now-old revision.
        _, coverage = collect([], {}, request['source_session_id'], request['review_source_date'],
                              locator=request['source_coverage'])
        if coverage != request['source_coverage']:
            raise ValueError('habit_baseline_sources_changed')
