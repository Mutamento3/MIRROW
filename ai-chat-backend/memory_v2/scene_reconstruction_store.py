"""Durable, rebuildable step receipts. No raw conversation or prompt copies."""
from contextlib import closing
from dataclasses import asdict
import json
import sqlite3
import time
from pathlib import Path

from .models import BatchSourceRef, digest_batch_sources
from .scene_reconstruction_contract import VERSION, validate_partition


class ReconstructionStore:
    def __init__(self, path, *, create=False):
        self.path = Path(path)
        if create:
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS scene_reconstruction_steps (
                      id TEXT PRIMARY KEY, refs TEXT NOT NULL, result TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS scene_reconstruction_attempts (
                      id TEXT PRIMARY KEY, attempts INTEGER NOT NULL, retry_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS scene_reconstruction_days (
                      active_date TEXT PRIMARY KEY, version TEXT NOT NULL, source_digest TEXT NOT NULL,
                      refs TEXT NOT NULL, result TEXT NOT NULL);
                ''')

    def _read(self, query, args):
        if not self.path.is_file():
            return None
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                return db.execute(query, args).fetchone()
        except sqlite3.Error:
            return None

    def step(self, key, refs, source):
        row = self._read('SELECT refs,result FROM scene_reconstruction_steps WHERE id=?', (key,))
        if not row:
            return None
        try:
            frozen = tuple(BatchSourceRef(**ref) for ref in json.loads(row[0]))
            if frozen != tuple(refs) or not source.validate_batch_sources(frozen):
                return None
            return json.loads(row[1])
        except (ValueError, TypeError, KeyError):
            return None

    def save_step(self, key, refs, value, source):
        if not source.validate_batch_sources(tuple(refs)):
            raise ValueError('scene_reconstruction_source_changed')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO scene_reconstruction_steps VALUES(?,?,?)',
                       (key, json.dumps([asdict(ref) for ref in refs]), json.dumps(value, ensure_ascii=False)))

    def claim(self, key, *, explicit_retry=False):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT attempts,retry_at FROM scene_reconstruction_attempts WHERE id=?', (key,)).fetchone()
            now = time.time()
            if row and not explicit_retry and (row[0] >= 3 or row[1] > now):
                return False
            db.execute('INSERT OR REPLACE INTO scene_reconstruction_attempts VALUES(?,?,?)',
                       (key, (row[0] if row and not explicit_retry else 0) + 1, now + 60))
            return True

    def load(self, day, refs, source, *, geometry=''):
        row = self._read('SELECT version,source_digest,refs,result FROM scene_reconstruction_days WHERE active_date=?', (day,))
        if not row or row[0] != VERSION + geometry or row[1] != digest_batch_sources(tuple(refs)):
            return None
        try:
            frozen = tuple(BatchSourceRef(**ref) for ref in json.loads(row[2]))
            if frozen != tuple(refs) or not source.validate_batch_sources(frozen):
                return None
            result = json.loads(row[3])
            validate_partition(result, refs)
            return result
        except (ValueError, TypeError, KeyError):
            return None

    def save(self, day, refs, result, source, *, geometry=''):
        validate_partition(result, refs)
        if not source.validate_batch_sources(tuple(refs)):
            raise ValueError('scene_reconstruction_source_changed')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO scene_reconstruction_days VALUES(?,?,?,?,?)',
                (day, VERSION + geometry, digest_batch_sources(tuple(refs)), json.dumps([asdict(ref) for ref in refs]),
                 json.dumps(result, ensure_ascii=False)))
