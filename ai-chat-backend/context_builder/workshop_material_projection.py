"""Reference repeated immutable workshop sources only within selected full history."""
from collections.abc import Mapping
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import sqlite3
from pathlib import Path

from .source_row_fields import calls, message_id


def _frozen(row):
    first = next(iter(calls(row)), {})
    extra = first.get('extra_data') or {}
    value = extra.get('memory_experience') if isinstance(extra, Mapping) else None
    if (not isinstance(value, Mapping) or value.get('version') != 2
            or not isinstance(value.get('materials', []), list)
            or any(not isinstance(m, Mapping) for m in value.get('materials', []))):
        return None
    return value


def read_node_experiences(rows, db_path):
    ids = {str(m.get('source_node_id')) for row in rows for m in (_frozen(row) or {}).get('materials', [])
           if m.get('kind') == 'workshop' and m.get('source_node_id')}
    if not ids or not Path(db_path).is_file():
        return {}
    found = {}
    try:
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            ordered = sorted(ids)
            for start in range(0, len(ordered), 400):
                batch = ordered[start:start + 400]
                for nid, aid, raw in db.execute('SELECT node_id,activity_id,source_payload FROM wander_nodes '
                        'WHERE node_id IN (' + ','.join('?' for _ in batch) + ')', batch):
                    payload = json.loads(raw or '{}')
                    value = payload.get('experience')
                    if isinstance(value, dict) and value.get('version') in (1, 2):
                        found[nid] = (aid, value)
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        return {}
    return found


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _parts(value):
    """Source identities and their exact public rendering; actions are excluded."""
    from workshop.experience import project
    for material in value.get('received') or []:
        room = material.get('room') or {}
        job = room.get('job') or {}
        for item in ([job] if job else material.get('jobs') or []):
            jid = item.get('id')
            if not jid or item.get('visibility') != 'public':
                continue
            for field, version, prefix in [('brief', 'revision', '当时的需求：'),
                                           ('plan', 'plan_version', '当时收到的方案正文：')]:
                if item.get(field) and item.get(version) is not None:
                    yield ('job', jid, field, item[version], _digest(item[field])), prefix + item[field], item, field
        if job.get('id') and job.get('visibility') == 'public':
            for item in list(room.get('messages') or []):
                if item.get('seq') is None or not item.get('created_at') or not item.get('role') or not item.get('body'):
                    continue
                isolated = {'version': value['version'], 'received': [{'room': {'messages': [item]}}]}
                yield ('message', job['id'], item['seq'], item['created_at'], item['role'], _digest(item)), project(isolated), room, item
        for item in list(material.get('historical_receipts') or []):
            if not item.get('receipt_id') or not item.get('job_id'):
                continue
            isolated = {'version': value['version'], 'received': [{'historical_receipts': [item]}]}
            body = project(isolated)
            if body:
                yield ('receipt', item['job_id'], item['receipt_id'], _digest(item)), body, material, item


def projection_overrides(rows, *, db_path, provided_texts, node_experiences=None):
    """Earlier full source proof; version drift/missing/truncated sources retain text."""
    from workshop.experience import project
    from .provider_presentation import experience_text
    nodes = read_node_experiences(rows, db_path) if node_experiences is None else node_experiences
    seen, overrides = {}, {}
    for row in rows:
        mid = message_id(row)
        frozen = _frozen(row)
        if row.get('role') != 'event' or row.get('event_type') != 'wander_activity' or not mid or not frozen:
            continue
        source_text = provided_texts.get(mid, '')
        selected = deepcopy(frozen)
        changed, additions = False, []
        reference = '工坊-' + hashlib.sha256(mid.encode()).hexdigest()[:12]
        sid = str(row.get('session_id') or '')
        if not sid:
            continue
        for material in selected.get('materials') or []:
            if material.get('kind') != 'workshop':
                continue
            node = nodes.get(str(material.get('source_node_id') or ''))
            if not node or mid != 'activity_event_' + str(node[0]):
                continue
            original = node[1]
            description = material.get('description') or ''
            try:
                full = project(original)
                current = deepcopy(original)
                parts = list(_parts(current))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not description or full != description or description not in source_text:
                continue
            refs = {}
            for key, body, owner, field in parts:
                if body not in description:
                    continue
                previous = seen.get((sid, *key))
                if previous:
                    if key[0] == 'job':
                        owner.pop(field, None)
                    elif key[0] == 'message':
                        owner['messages'].remove(field)
                    else:
                        owner['historical_receipts'].remove(field)
                    refs.setdefault(previous, set()).add({'job': '需求／方案', 'message': '房间旧原话', 'receipt': '旧回执'}[key[0]])
                else:
                    additions.append(((sid, *key), body))
            if refs:
                notes = [f'本次再次收到的{"、".join(sorted(kinds))}与前面的[{label}]逐字相同，完整正文见该记录。'
                         for label, kinds in refs.items()]
                candidate = project(current) + '\n' + '\n'.join(notes)
                if len(candidate) < len(description):
                    material['description'] = candidate
                    changed = True
        if changed:
            first = next(iter(calls(row)), {})
            text = 'AI 的漫想行动记录：' + str(first.get('description') or '一次漫想活动已结算') + '。\n' + experience_text(selected)
        else:
            text = source_text
        # Only genuinely retained full fragments establish future coverage;
        # references never become reference chains. Identity labels are stable.
        admitted = [(key, body) for key, body in additions if body in text]
        if admitted or changed:
            text = f'[{reference}]\n' + text
            overrides[mid] = text
        for key, body in admitted:
            seen.setdefault(key, reference)
    return overrides


def reproject_selected_messages(messages):
    """Builder may trim/exclude preprojected memories: prove references again."""
    from copy import copy
    from .event_history_adapter import adapt_history_for_context
    from .provider_presentation import internal_event_message
    if not any('[工坊-' in str(getattr(m, 'content', '')) for m in messages):
        return messages
    rows = []
    for message in messages:
        meta = getattr(message, 'metadata', {})
        if meta.get('event_type') == 'wander_activity' and meta.get('provider_internal'):
            row = {**meta, 'role': 'event'}
            if _frozen(row):
                rows.append(row)
    full = adapt_history_for_context(rows, for_provider=True, compact_linked_sources=False)
    texts = {message_id(row): row['content'] for row in full}
    overrides = projection_overrides(rows,
        db_path=Path(__file__).resolve().parents[1] / 'events' / 'wander_runtime.db', provided_texts=texts)
    restored = dict(texts)
    for row in rows:
        mid = message_id(row)
        if mid in overrides:
            restored[mid] = internal_event_message(row, overrides[mid])['content']
    result = []
    for message in messages:
        replacement = restored.get(str(getattr(message, 'metadata', {}).get('message_id') or ''))
        if replacement is None:
            result.append(message)
        else:
            owned = copy(message)
            owned.content = replacement
            result.append(owned)
    return result
