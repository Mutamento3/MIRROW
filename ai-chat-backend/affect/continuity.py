"""Read the current uninterrupted feeling episode, never the reused row's age."""
import json
import sqlite3
from datetime import datetime

from .active_day import LOCAL_TZ
from .reported_state import reported_state


def episode_view(db, folder, item, **kwargs):
    try:
        return _episode_view(db, folder, item, **kwargs)
    except (ValueError, TypeError, KeyError, sqlite3.Error):
        return dict(item, source_refs=[], episode_started_at='')


def _episode_view(db, folder, item, *, normalize_label, parse_time, effective, cutoff):
    view = dict(item)
    head = item.get('parent_transition_id')
    if not head:
        return view
    refs = []
    seen = set()
    for _ in range(256):
        if not head or head in seen:
            break
        seen.add(head)
        row = db.execute('SELECT * FROM affect_transitions WHERE transition_id=?', (head,)).fetchone()
        if (not row or row['component_id'] != item['component_id']
                or row['owner'] != item['owner'] or row['status'] != 'accepted'
                or row['source_invalidated'] or row['operation'] not in {'add', 'reinforce', 'reappraise'}):
            break
        before, after = json.loads(row['before_json']), json.loads(row['after_json'])
        if normalize_label(after.get('label')) != normalize_label(item.get('label')):
            break
        current_refs = json.loads(row['source_refs_json'])
        # Revoked roots cannot establish an earlier origin for a current feeling.
        for ref in current_refs:
            source = db.execute('SELECT status FROM affect_source_revisions WHERE source_type=? AND source_id=? AND source_revision=?',
                (ref.get('source_type'), ref.get('source_id'), ref.get('source_revision'))).fetchone()
            if source and source['status'] == 'active':
                refs.append(ref)
        when = after.get('last_reinforced_at') or after.get('started_at')
        if not when:
            break
        view['episode_started_at'] = when
        prior = reported_state(db, folder, before, normalize_label=normalize_label, parse_time=parse_time)
        if (not before or row['operation'] == 'add'
                or normalize_label(before.get('label')) != normalize_label(after.get('label'))
                or before.get('status') not in {'active', 'provisional'}
                or effective(prior, when) < cutoff):
            view['source_refs'] = refs
            return view
        head = row['parent_transition_id']
    # Incomplete lineage is not proof that a feeling has lasted for days.
    view['source_refs'] = []
    view['episode_started_at'] = ''
    return view


def continuity_text(origin, current_day, now):
    if not origin or not current_day or origin >= current_day:
        return ''
    try:
        clock = datetime.fromisoformat(now.replace('Z', '+00:00'))
        clock = clock.replace(tzinfo=LOCAL_TZ) if clock.tzinfo is None else clock.astimezone(LOCAL_TZ)
        days = (clock.date() - datetime.fromisoformat(origin).date()).days
    except (ValueError, TypeError):
        return ''
    start = '昨天' if days == 1 else '前天' if days == 2 else f'{days}天前'
    return f'从{start}一直延续到今天' if days > 0 else ''
