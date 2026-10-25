"""Append/update one metadata row per real request. No prompts, responses or URLs."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from .response_diagnostics import FIELDS, sanitize
from llm_usage import extract_token_usage
DB_PATH = Path(__file__).resolve().parents[1] / 'events' / 'api_calls.db'
logger = logging.getLogger(__name__)

def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')

@contextmanager
def _db(path: Path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute("CREATE TABLE IF NOT EXISTS attempts (\n        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, category TEXT NOT NULL,\n        node TEXT NOT NULL, station_id TEXT NOT NULL, station_name TEXT NOT NULL,\n        source_id TEXT NOT NULL, model TEXT NOT NULL, ordinal INTEGER NOT NULL,\n        started_at TEXT NOT NULL, ended_at TEXT, duration_ms INTEGER,\n        status TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '',\n        http_status INTEGER NOT NULL DEFAULT 0, trace_id TEXT NOT NULL DEFAULT '',\n        input_tokens INTEGER, output_tokens INTEGER)")
    columns = {row[1] for row in db.execute('PRAGMA table_info(attempts)')}
    for name, sql_type in FIELDS.items():
        if name not in columns:
            try:
                db.execute(f'ALTER TABLE attempts ADD COLUMN {name} {sql_type}')
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise
    for name in ('cached_input_tokens', 'cache_write_input_tokens', 'cache_write_5m_tokens', 'cache_write_1h_tokens'):
        if name not in columns:
            try:
                db.execute(f'ALTER TABLE attempts ADD COLUMN {name} INTEGER')
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise
    db.execute('CREATE INDEX IF NOT EXISTS attempts_started ON attempts(started_at)')
    db.execute('CREATE INDEX IF NOT EXISTS attempts_task ON attempts(task_id)')
    try:
        with db:
            yield db
    finally:
        db.close()

def begin(task_id: str, category: str, node: str, source: dict, ordinal: int=1, path: Path=DB_PATH) -> dict:
    handle = {'id': uuid.uuid4().hex, 'started': time.perf_counter(), 'path': path}
    try:
        with _db(path) as db:
            db.execute('INSERT INTO attempts(id,task_id,category,node,station_id,station_name,source_id,model,ordinal,started_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (handle['id'], task_id, category, node, source['station_id'], source['station_name'], source['source_id'], source['model'], ordinal, now_iso(), 'running'))
    except Exception:
        logger.warning('API attempt diagnostics unavailable')
    return handle

def finish(handle: dict | None, status: str, *, error_code: str='', http_status: int=0, trace_id: str='', usage: dict | None=None, response_metadata: dict | None=None) -> None:
    if not handle:
        return
    usage = usage if isinstance(usage, dict) else {}
    error_code = error_code if isinstance(error_code, str) else 'validation_error'
    counts = extract_token_usage(usage)
    trace = trace_id if re.fullmatch('[A-Za-z0-9_.:-]{1,128}', str(trace_id)) else ''
    code = error_code if re.fullmatch('[a-zA-Z0-9_:-]{0,100}', error_code) else 'validation_error'
    try:
        with _db(handle['path']) as db:
            db.execute('UPDATE attempts SET ended_at=?,duration_ms=?,status=?,error_code=?,http_status=?,trace_id=?,input_tokens=?,output_tokens=?,cached_input_tokens=?,cache_write_input_tokens=?,cache_write_5m_tokens=?,cache_write_1h_tokens=? WHERE id=?', (now_iso(), round((time.perf_counter() - handle['started']) * 1000), status, code, http_status, trace, counts['prompt'], counts['completion'], counts['cache_hit'], counts['cache_write'], counts['cache_write_5m'], counts['cache_write_1h'], handle['id']))
            if response_metadata is not None:
                values = sanitize(response_metadata)
                if values.get('reasoning_tokens') is None:
                    values['reasoning_tokens'] = counts['reasoning']
                db.execute('UPDATE attempts SET ' + ','.join((key + '=?' for key in FIELDS)) + ' WHERE id=?', [*(values[key] for key in FIELDS), handle['id']])
            elif counts['reasoning'] is not None:
                db.execute('UPDATE attempts SET reasoning_tokens=? WHERE id=?', (counts['reasoning'], handle['id']))
    except Exception:
        logger.warning('API attempt diagnostics unavailable')

def identify_source(api_url: str, model: str, api_key: str='') -> dict:
    from .config import load_config
    from main_model_profiles import get_main_model_profiles, normalize_openai_base_url
    normalized = normalize_openai_base_url(api_url)
    try:
        config, env, _ = load_config()
        for station in config.stations:
            if normalize_openai_base_url(station.base_url) == normalized and (not api_key or env.get(station.key_env) == api_key):
                match = next((item for item in station.models if item.model == model), None)
                return {'station_id': station.id, 'station_name': station.name, 'source_id': match.id if match else station.id + '_manual', 'model': model}
        for profile in get_main_model_profiles(env).values():
            if profile.base_url == normalized and (not api_key or profile.api_key == api_key):
                return {'station_id': 'main_' + profile.id, 'station_name': profile.label + ' 来源', 'source_id': profile.id, 'model': model}
    except Exception:
        pass
    return {'station_id': 'legacy', 'station_name': '现有独立来源', 'source_id': 'legacy', 'model': model}

def snapshot(*, since: str='', until: str='', category: str='', node: str='', status: str='', station_id: str='', page: int=0, path: Path=DB_PATH) -> dict:
    from .catalog import NODE_LABELS, AUDIT_NODE_LABELS, category_labels
    conditions, args = ([], [])
    for field, value in (('category', category), ('node', node), ('status', status), ('station_id', station_id)):
        if value:
            conditions.append(field + '=?')
            args.append(value)
    if since:
        conditions.append('started_at>=?')
        args.append(since)
    if until:
        conditions.append('started_at<?')
        args.append(until)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    with _db(path) as db:
        summary = [dict(row) for row in db.execute("SELECT category,node,station_id,station_name,source_id,model,\n            COUNT(*) calls, SUM(status='ok') success, SUM(status NOT IN ('ok','refusal','running')) failures,\n            SUM(status='refusal') refusals, SUM(status='running') running,\n            SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,\n            SUM(CASE WHEN input_tokens IS NOT NULL AND cached_input_tokens BETWEEN 0 AND input_tokens\n                THEN cached_input_tokens ELSE NULL END) cached_input_tokens,\n            SUM(cache_write_input_tokens) cache_write_input_tokens,\n            SUM(cache_write_5m_tokens) cache_write_5m_tokens,\n            SUM(cache_write_1h_tokens) cache_write_1h_tokens,\n            SUM(CASE WHEN input_tokens IS NOT NULL AND cached_input_tokens BETWEEN 0 AND input_tokens\n                THEN input_tokens ELSE NULL END) cache_reported_input_tokens\n            FROM attempts" + where + ' GROUP BY category,node,station_id,station_name,source_id,model ORDER BY calls DESC', args)]
        total = db.execute('SELECT COUNT(DISTINCT task_id) FROM attempts' + where, args).fetchone()[0]
        task_ids = [row[0] for row in db.execute('SELECT task_id FROM attempts' + where + ' GROUP BY task_id ORDER BY MAX(started_at) DESC LIMIT 20 OFFSET ?', [*args, page * 20])]
        tasks = []
        for task_id in task_ids:
            attempts = [dict(row) for row in db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started_at,ordinal', (task_id,))]
            tasks.append({'id': task_id, 'attempts': attempts, 'status': attempts[-1]['status']})
        return {'summary': summary, 'tasks': tasks, 'total_tasks': total, 'page': page, 'page_size': 20, 'category_labels': category_labels(), 'updated_at': now_iso(), 'nodes': [row[0] for row in db.execute('SELECT DISTINCT node FROM attempts ORDER BY node')], 'node_labels': {**NODE_LABELS, **AUDIT_NODE_LABELS, 'chat_final_reply': '主聊天回复', 'main_chat': '主大脑', 'wander_message': '漫想 · 最终分享', 'connection_test': '连接测试', 'visual_reply': '视觉回复'}, 'stations': [dict(row) for row in db.execute('SELECT station_id id,MAX(station_name) name FROM attempts GROUP BY station_id ORDER BY name')]}
