"""Idempotent native samples. Measured time is never replaced by upload time."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone

DB_PATH = Path(__file__).resolve().parents[1] / 'events' / 'miband.db'
TZ = timezone(timedelta(hours=8))


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS samples (measured_at REAL NOT NULL, lane TEXT NOT NULL, payload TEXT NOT NULL, synced_at REAL NOT NULL, PRIMARY KEY(measured_at,lane))')
    try:
        with db:
            yield db
    finally:
        db.close()


def ingest(batch):
    now = time.time()
    with connect() as db:
        for item in batch.samples:
            lane = 'realtime' if item.raw_kind == -1 else 'activity'
            db.execute('INSERT INTO samples VALUES (?,?,?,?) ON CONFLICT(measured_at,lane) DO UPDATE SET payload=excluded.payload,synced_at=excluded.synced_at',
                       (item.measured_at, lane, json.dumps(item.model_dump()), now))
    return {'accepted': len(batch.samples)}


def overview(now=None):
    now = time.time() if now is None else now
    if not DB_PATH.exists():
        return {'available': False, 'source': 'mi_band_7', 'extra_metrics': extra_metrics()}
    with connect() as db:
        rows = db.execute('SELECT * FROM samples WHERE measured_at>=? AND measured_at<=? ORDER BY measured_at', (now-8*86400, now)).fetchall()
    if not rows:
        return {'available': False, 'source': 'mi_band_7', 'extra_metrics': extra_metrics()}
    samples = [dict(json.loads(r['payload']), lane=r['lane']) for r in rows]
    activity = [s for s in samples if s['lane'] == 'activity']
    heart = [s for s in samples if 20 <= s['heart_rate'] <= 240]
    today = datetime.fromtimestamp(now, TZ).date()
    start = datetime.combine(today, datetime.min.time(), TZ).timestamp()
    day_samples = [s for s in activity if s['measured_at'] >= start]
    hr = heart[-1] if heart else None
    return {
        'available': True, 'source': 'mi_band_7',
        'latest_sample_at': rows[-1]['measured_at'],
        'last_sync_at': max(r['synced_at'] for r in rows),
        'heart_rate': None if hr is None else {'value': hr['heart_rate'], 'measured_at': hr['measured_at'], 'stale': now-hr['measured_at']>300},
        'steps': None if not day_samples else {'value': sum(s['steps'] for s in day_samples), 'date': str(today), 'measured_at': day_samples[-1]['measured_at'], 'coverage_start': day_samples[0]['measured_at'], 'kind': 'synced_minute_sum', 'stale': now-day_samples[-1]['measured_at']>1800},
        'sleep': sleep_summary(activity), 'extra_metrics': extra_metrics(),
    }


def extra_metrics():
    # Transport must implement actual measurements before claiming availability.
    return {key: {'available': False, 'reason': '当前移植的协议采集尚未接通'} for key in ('spo2', 'stress', 'pai', 'respiratory_rate')}


def heart_stats(seconds=15):
    seconds = max(5, min(300, int(seconds)))
    if not DB_PATH.exists():
        return None
    now = time.time()
    with connect() as db:
        rows = db.execute('SELECT payload FROM samples WHERE measured_at BETWEEN ? AND ? ORDER BY measured_at', (now-seconds, now)).fetchall()
    values = [json.loads(r['payload'])['heart_rate'] for r in rows]
    values = [v for v in values if 20 <= v <= 240]
    if not values:
        return None
    return {'source':'mi_band_7', 'window_seconds':seconds, 'count':len(values), 'min':min(values),
            'max':max(values), 'avg':round(sum(values)/len(values),1), 'latest':values[-1]}


def latest_sleep_stage(now=None, max_lookback_seconds=6 * 3600, fresh_seconds=30 * 60):
    """Return the newest device sleep-minute observation with honest age.

    Activity history arrives in batches, so ``synced_at`` is intentionally not
    used as freshness.  A newly uploaded old sample remains an old observation.
    The native Huami record identifies sleep minutes, but its deep/light/REM
    hint bytes need vendor-side post-processing and are not exposed as stages.
    """
    now = time.time() if now is None else float(now)
    if not DB_PATH.exists():
        return None
    with connect() as db:
        rows = db.execute(
            'SELECT measured_at,payload,synced_at FROM samples '
            'WHERE lane=? AND measured_at BETWEEN ? AND ? ORDER BY measured_at DESC',
            ('activity', now - max_lookback_seconds, now),
        ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row['payload'])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        stage_hint = payload.get('sleep_stage')
        if payload.get('raw_kind') != 120 and stage_hint not in ('light', 'deep', 'rem'):
            continue
        age = max(0.0, now - float(row['measured_at']))
        return {
            'stage': 'sleep',
            'measured_at': float(row['measured_at']),
            'synced_at': float(row['synced_at']),
            'age_seconds': age,
            'fresh': age <= fresh_seconds,
            'source': 'mi_band_7_activity_sleep_kind',
            'stage_breakdown_available': False,
            'stage_breakdown_reason': '原生记录可确认睡眠分钟，分期提示未经厂商算法校准',
        }
    return None


def sleep_summaries(activity, now=None, completed_only=False):
    """Return device sleep sessions grouped by the local date they end on.

    The band uploads minute history in batches.  A session is complete only
    after later activity has been observed (or its end date is already in the
    past); this prevents an in-progress night from entering the personal
    baseline as an artificially short sleep.
    """
    now = time.time() if now is None else float(now)
    current_date = datetime.fromtimestamp(now, TZ).date()
    latest_activity_at = max(
        (float(sample.get('measured_at') or 0) for sample in activity),
        default=0,
    )
    segments = []
    for sample in activity:
        # raw_kind=120 is the native sleep-minute marker.  sleep_stage is kept
        # only for compatibility with already-uploaded AionsHome-derived rows;
        # its deep/light/REM interpretation is not treated as calibrated fact.
        if sample.get('raw_kind') != 120 and sample.get('sleep_stage') not in ('light', 'deep', 'rem'):
            continue
        if not segments or sample['measured_at']-segments[-1][-1]['measured_at']>600:
            segments.append([])
        segments[-1].append(sample)
    if not segments:
        return {}
    by_date = {}
    for segment in segments:
        end_at = segment[-1]['measured_at'] + 60
        date = datetime.fromtimestamp(end_at - 1, TZ).date()
        complete = date < current_date or latest_activity_at - segment[-1]['measured_at'] > 600
        if completed_only and not complete:
            continue
        session = {
            'start_at': segment[0]['measured_at'],
            'end_at': end_at,
            'complete': complete,
            'total_sleep_min': len(segment),
        }
        by_date.setdefault(str(date), []).append(session)

    result = {}
    for date, sessions in by_date.items():
        result[date] = {
            'date': date,
            'source': 'mi_band_7_activity_sleep_kind',
            'sessions': sessions,
            'complete': all(item['complete'] for item in sessions),
            'completion': '设备历史睡眠分钟；不能单凭末条记录确认已经醒来',
            'total_sleep_min': sum(s['total_sleep_min'] for s in sessions),
            'stage_breakdown_available': False,
            'stage_breakdown_reason': '原生提示字节需要厂商后处理，当前不输出深睡/浅睡/REM结论',
        }
    return result


def sleep_summary(activity, now=None):
    # Adapted from AionsHome routes/health.py (MIT): minute stages, 10-minute segmentation.
    summaries = sleep_summaries(activity, now=now)
    if not summaries:
        return None
    return summaries[sorted(summaries)[-1]]


def sleep_summary_for_date(date, now=None, completed_only=True):
    """Read one local end-date summary directly from the native sample store."""
    now = time.time() if now is None else float(now)
    if not DB_PATH.exists():
        return None
    try:
        target = datetime.strptime(str(date), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None
    day_start = datetime.combine(target, datetime.min.time(), TZ).timestamp()
    day_end = day_start + 86400
    # A night ending on target may start the previous evening.  Include one
    # full day on each side so segmentation can also see later wake activity.
    with connect() as db:
        rows = db.execute(
            'SELECT measured_at,payload FROM samples WHERE lane=? '
            'AND measured_at BETWEEN ? AND ? ORDER BY measured_at',
            ('activity', day_start - 86400, day_end + 86400),
        ).fetchall()
    activity = []
    for row in rows:
        try:
            activity.append(dict(json.loads(row['payload']), measured_at=row['measured_at']))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return sleep_summaries(
        activity, now=now, completed_only=completed_only,
    ).get(str(target))
