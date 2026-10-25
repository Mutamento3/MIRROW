"""Display stages from official message phases; canonical room text is unchanged."""
import json
import sqlite3
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .protocol import dialogue

PHASES = {'commentary', 'final_answer'}


@lru_cache(maxsize=32)
def _rollout_phases(filename, thread_id, stamp, size):
    phases = defaultdict(set)
    identified = False
    try:
        with Path(filename).open(encoding='utf-8') as stream:
            for line in stream:
                if len(line) > 8 * 1024 * 1024:
                    return {}
                entry = json.loads(line)
                payload = entry.get('payload') or {}
                if entry.get('type') == 'session_meta':
                    identified = payload.get('id') == thread_id
                if (not identified or entry.get('type') != 'response_item'
                        or payload.get('type') != 'message' or payload.get('role') != 'assistant'
                        or payload.get('phase') not in PHASES):
                    continue
                # Never read or project reasoning/function/tool payloads.
                raw = '\n'.join(part.get('text', '') for part in payload.get('content', [])
                                if part.get('type') == 'output_text')
                body, _ = dialogue(raw)
                if body:
                    phases[body].add(payload['phase'])
        return {body: next(iter(values)) for body, values in phases.items() if len(values) == 1}
    except (OSError, ValueError, TypeError):
        return {}


def legacy_phases(store, thread_id):
    home = (store.directory / 'codex-home').resolve()
    if not thread_id or not (home / 'state_5.sqlite').is_file():
        return {}
    try:
        with sqlite3.connect((home / 'state_5.sqlite').as_uri() + '?mode=ro', uri=True, timeout=.5) as db:
            db.execute('PRAGMA query_only=ON')
            row = db.execute('SELECT rollout_path FROM threads WHERE id=?', (thread_id,)).fetchone()
        if not row:
            return {}
        path = Path(row[0]).resolve()
        stat = path.stat()
        if not path.is_relative_to(home) or stat.st_size > 64 * 1024 * 1024:
            return {}
        return _rollout_phases(str(path), thread_id, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, sqlite3.Error):
        return {}


def messages(store, job, rows):
    """Only the public display view adds phase metadata; never edits old messages."""
    with store.db() as db:
        receipts = db.execute("SELECT body FROM receipts WHERE job_id=? AND kind='reply'", (job['id'],)).fetchall()
    phases = {}
    for row in receipts:
        body = json.loads(row['body'])
        if body.get('phase') in PHASES and type(body.get('message_seq')) is int:
            phases[body['message_seq']] = body['phase']
    legacy = legacy_phases(store, job.get('thread_id')) if any(
        row['role'] == 'codex' and row['seq'] not in phases for row in rows) else {}
    return [{**row, 'phase': phases.get(row['seq']) or legacy.get(row['body']) or 'unclassified'}
            if row['role'] == 'codex' else dict(row) for row in rows]
