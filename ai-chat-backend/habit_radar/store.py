"""Local shadow receipts. No cognition authority, prompt bodies or reasoning."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .day_material import ContextSource
from .extraction import Signal


def decode_signals(value: str) -> tuple[Signal, ...]:
    result = []
    for item in json.loads(value):
        if item.get("context_source"):
            item["context_source"] = ContextSource(**item["context_source"])
        result.append(Signal(**item))
    return tuple(result)


class ShadowStore:
    def __init__(self, path: Path, *, create: bool = False):
        self.path = Path(path).resolve()
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS radar_days (
                        active_date TEXT PRIMARY KEY, revision TEXT NOT NULL,
                        status TEXT NOT NULL, attempt_day TEXT NOT NULL,
                        attempts INTEGER NOT NULL, next_retry REAL NOT NULL,
                        updated_at REAL NOT NULL, error_code TEXT NOT NULL DEFAULT '',
                        batch_keys TEXT NOT NULL DEFAULT '[]', report TEXT NOT NULL DEFAULT '{}');
                    CREATE TABLE IF NOT EXISTS radar_batches (
                        batch_key TEXT PRIMARY KEY, signals TEXT NOT NULL,
                        report TEXT NOT NULL, created_at REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS radar_calls (
                        id INTEGER PRIMARY KEY, active_date TEXT NOT NULL,
                        batch_key TEXT NOT NULL, created_at REAL NOT NULL,
                        status TEXT NOT NULL, usage TEXT NOT NULL);
                """)

    @contextmanager
    def connect(self, *, readonly: bool = False):
        uri = f"{self.path.as_uri()}?mode=ro" if readonly else str(self.path)
        db = sqlite3.connect(uri, uri=readonly, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def days(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.connect(readonly=True) as db:
            return [dict(row) for row in db.execute("SELECT * FROM radar_days ORDER BY active_date DESC")]

    def claim(self, day: str, revision: str, *, now: float, attempt_day: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM radar_days WHERE active_date=?", (day,)).fetchone()
            attempts = 0
            if old:
                # A live lease protects even a different source revision.
                if old["status"] == "running" and old["next_retry"] > now:
                    return False
                if old["revision"] == revision:
                    if old["status"] == "complete":
                        return False
                    attempts = old["attempts"] if old["attempt_day"] == attempt_day else 0
                    if attempts >= 3 or old["next_retry"] > now:
                        return False
            db.execute("""INSERT INTO radar_days
                (active_date, revision, status, attempt_day, attempts, next_retry, updated_at)
                VALUES (?, ?, 'running', ?, ?, ?, ?)
                ON CONFLICT(active_date) DO UPDATE SET revision=excluded.revision,
                status='running', attempt_day=excluded.attempt_day, attempts=excluded.attempts,
                next_retry=excluded.next_retry, updated_at=excluded.updated_at,
                error_code='', batch_keys='[]', report='{}'""",
                (day, revision, attempt_day, attempts + 1, now + 3600, now))
            return True

    def cached(self, key: str):
        with self.connect(readonly=True) as db:
            row = db.execute("SELECT signals, report FROM radar_batches WHERE batch_key=?", (key,)).fetchone()
        return (decode_signals(row["signals"]), json.loads(row["report"])) if row else None

    def save_batch(self, key, signals, report, *, now):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO radar_batches VALUES (?, ?, ?, ?)",
                       (key, json.dumps([asdict(s) for s in signals], ensure_ascii=False),
                        json.dumps(report), now))

    def record_call(self, day, key, status, usage, *, now):
        with self.connect() as db:
            db.execute("INSERT INTO radar_calls(active_date,batch_key,created_at,status,usage) VALUES(?,?,?,?,?)",
                       (day, key, now, status, json.dumps(usage)))

    def finish(self, day, revision, *, now, report, keys=(), error=""):
        with self.connect() as db:
            changed = db.execute("""UPDATE radar_days SET status=?, updated_at=?,
                next_retry=?, report=?, batch_keys=?, error_code=?
                WHERE active_date=? AND revision=? AND status='running'""",
                ("error" if error else "complete", now, now + 3600 if error else 0,
                 json.dumps(report), json.dumps(keys), error, day, revision)).rowcount
            if changed != 1:
                raise RuntimeError("shadow_claim_lost")

    def signals_for(self, day: str) -> tuple[Signal, ...]:
        with self.connect(readonly=True) as db:
            row = db.execute("SELECT batch_keys,status FROM radar_days WHERE active_date=?", (day,)).fetchone()
        if not row or row["status"] != "complete":
            return ()
        return tuple(signal for key in json.loads(row["batch_keys"])
                     for signal in self.cached(key)[0])

    def call_summary(self) -> dict:
        if not self.path.exists():
            return {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        with self.connect(readonly=True) as db:
            rows = db.execute("SELECT usage,status FROM radar_calls").fetchall()
        result = {"model_calls": len(rows), "failed_calls": sum(r["status"] != "ok" for r in rows),
                  "prompt_tokens": 0, "completion_tokens": 0,
                  "usage_scope": "provider_last_response_per_call; transport_retries_may_cost_extra"}
        for row in rows:
            usage = json.loads(row["usage"])
            for key in ("prompt_tokens", "completion_tokens"):
                result[key] += int(usage.get(key) or 0)
        return result
