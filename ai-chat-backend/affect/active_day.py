"""Read authoritative day labels without initializing or changing other stores."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

LOCAL_TZ = timezone(timedelta(hours=8))


def _day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return ''


def read_active_days(folder, items, *, session_id='', now=''):
    """Return current day, first and latest evidenced days; unknown stays unknown.

    The first root describes continuity. The latest root describes whether a
    genuine new observation can support current-day foreground detail.
    """
    folder = Path(folder)
    refs = {item['component_id']: [ref for ref in item.get('source_refs', [])
            if ref.get('relation') in {'self_report', 'causal'}] for item in items}
    messages = {ref['source_id'] for group in refs.values() for ref in group
                if ref.get('source_type') == 'conversation_message'}
    activities = {ref['source_id'] for group in refs.values() for ref in group
                  if ref.get('source_type') == 'wander_settlement'}
    source_days = {}
    current = ''
    path = folder / 'event_chronicle.db'
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            topics = db.execute('SELECT start_time FROM topic_state WHERE is_active=1'
                + (' AND session_id=?' if session_id else ''), (session_id,) if session_id else ()).fetchall()
            if len(topics) == 1 and topics[0][0]:
                started = datetime.fromisoformat(topics[0][0].replace('Z', '+00:00'))
                current = started.replace(tzinfo=LOCAL_TZ).date().isoformat() if started.tzinfo is None else started.astimezone(LOCAL_TZ).date().isoformat()
            elif not topics:
                clock = datetime.fromisoformat(now.replace('Z', '+00:00')) if now else datetime.now(LOCAL_TZ)
                current = clock.replace(tzinfo=LOCAL_TZ).date().isoformat() if clock.tzinfo is None else clock.astimezone(LOCAL_TZ).date().isoformat()
            ids = sorted(messages)
            for start in range(0, len(ids), 300):
                batch = ids[start:start + 300]
                rows = db.execute('SELECT message_id,active_date FROM conversation_messages WHERE message_id IN ('
                                  + ','.join('?' for _ in batch) + ')', batch)
                source_days.update({('conversation_message', mid): _day(day) for mid, day in rows})
    except (OSError, sqlite3.Error, ValueError, TypeError):
        # A partial read is not enough to establish an active-day boundary.
        return '', {}, {}
    if activities:
        path = folder / 'wander_runtime.db'
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.execute('PRAGMA query_only=ON')
                ids = sorted(activities)
                for start in range(0, len(ids), 300):
                    batch = ids[start:start + 300]
                    rows = db.execute("SELECT activity_id,active_date FROM wander_event_projections WHERE status='sent' AND activity_id IN ("
                                      + ','.join('?' for _ in batch) + ')', batch)
                    source_days.update({('wander_settlement', aid): _day(day) for aid, day in rows})
        except (OSError, sqlite3.Error):
            pass
    origins = {}
    latest = {}
    for cid, group in refs.items():
        days = [source_days.get((ref.get('source_type'), ref.get('source_id'))) for ref in group]
        known = [day for day in days if day]
        if known:
            origins[cid] = min(known)
            latest[cid] = max(known)
    return current, origins, latest
