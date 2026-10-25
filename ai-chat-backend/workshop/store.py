"""Transactional queue, rooms, immutable receipts and cumulative usage ledger."""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

MODELS = {"gpt-6-astra": "6.0 Astra", "gpt-6.1-sol": "6.1 Sol"}
DEFAULTS = {"model": "gpt-6.1-sol", "remaining_floor": 30, "token_budget": 100000,
            "target_percent": 10, "budget_mode": "percent", "visit_rounds": 4, "enabled": True,
            "review_model": "gpt-6-luna", "review_token_budget": 30000, "desktop_idle_only": True, "budget_limits_enabled": True,
            "reasoning_effort": "high", "review_reasoning_effort": "medium", "max_parallel": 1}
HOLDING = ("discussing", "awaiting_input", "awaiting_confirmation", "executing", "awaiting_review", "stopping", "recovery_required")


def now():
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.getenv("MIRROW_WORKSHOP_HOME") or Path.home() / ".mirrow-workshop").resolve()
        self.lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, brief TEXT NOT NULL, state TEXT NOT NULL,
                    queue_seq INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    plan_version INTEGER NOT NULL DEFAULT 0, plan TEXT NOT NULL DEFAULT '',
                    confirmed_version INTEGER NOT NULL DEFAULT 0, thread_id TEXT NOT NULL DEFAULT '',
                    turn_id TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                    tokens INTEGER NOT NULL DEFAULT 0, token_cap INTEGER NOT NULL DEFAULT 0,
                    usage_known INTEGER NOT NULL DEFAULT 1, pending_text TEXT NOT NULL DEFAULT '',
                    resume_state TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL, projected INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS usage (thread_id TEXT PRIMARY KEY, watermark INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL,
                    summary TEXT NOT NULL, checks TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS budget_samples (id TEXT PRIMARY KEY, model TEXT NOT NULL,
                    bucket TEXT NOT NULL, window_mins INTEGER NOT NULL, token_delta INTEGER NOT NULL,
                    percent_delta REAL NOT NULL, created_at TEXT NOT NULL);
            ''')
            db.execute("INSERT OR IGNORE INTO settings VALUES (1,?)", (json.dumps(DEFAULTS),))
            if 'visibility' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")
            if 'budget' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN budget TEXT NOT NULL DEFAULT '{}'")
            if 'reasoning_effort' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT ''")
        from .quality import install
        install(self)
        from .cabinet import install as install_cabinet
        install_cabinet(self)

    @contextlib.contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.directory / "workshop.sqlite3", timeout=20)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def settings(self):
        with self.db() as db:
            saved = json.loads(db.execute("SELECT body FROM settings WHERE id=1").fetchone()[0])
            return {**DEFAULTS, 'budget_mode': saved.get('budget_mode', 'tokens'), **saved}

    def configure(self, data):
        allowed = set(DEFAULTS)
        if set(data) - allowed:
            raise ValueError("unknown_setting")
        cfg = {**self.settings(), **data}
        if type(cfg['max_parallel']) is not int or cfg['max_parallel'] not in (1, 2):
            raise ValueError('invalid_parallel_limit')
        from .reasoning import EFFORTS
        if cfg['reasoning_effort'] not in EFFORTS or cfg['review_reasoning_effort'] not in EFFORTS:
            raise ValueError('invalid_reasoning_effort')
        from .quality import MODELS as REVIEW_MODELS
        if cfg['review_model'] not in REVIEW_MODELS or type(cfg['review_token_budget']) is not int or not 1000 <= cfg['review_token_budget'] <= 100000:
            raise ValueError('invalid_review_setting')
        if cfg["model"] not in MODELS or not 0 <= cfg["remaining_floor"] <= 100:
            raise ValueError("invalid_model_or_floor")
        if not isinstance(cfg["token_budget"], int) or not 1000 <= cfg["token_budget"] <= 10000000:
            raise ValueError("invalid_token_budget")
        if not isinstance(cfg["visit_rounds"], int) or not 1 <= cfg["visit_rounds"] <= 8:
            raise ValueError("invalid_visit_rounds")
        from .budget import percent
        percent(cfg['target_percent'])
        if cfg['budget_mode'] not in ('tokens', 'percent') or type(cfg["enabled"]) is not bool or type(cfg['desktop_idle_only']) is not bool or type(cfg['budget_limits_enabled']) is not bool:
            raise ValueError("invalid_setting")
        old_cfg = self.settings()
        with self.db() as db:
            db.execute("UPDATE settings SET body=? WHERE id=1", (json.dumps(cfg),))
        if not cfg['desktop_idle_only'] or cfg['max_parallel'] != old_cfg['max_parallel']:
            from .account_meter import disable
            disable(self)
        return cfg

    def _event(self, db, job_id, kind, body):
        if job_id and kind == 'budget_paused':
            from .budget import summary
            current = dict(db.execute('SELECT tokens,token_cap,budget FROM jobs WHERE id=?', (job_id,)).fetchone())
            current['budget'] = json.loads(current['budget'])
            body = {**body, 'budget_summary': summary(current)}
        event_id = uuid.uuid4().hex
        db.execute("INSERT INTO receipts(id,job_id,kind,body,created_at) VALUES(?,?,?,?,?)",
                   (event_id, job_id, kind, json.dumps(body, ensure_ascii=False), now()))
        return event_id

    def event(self, job_id, kind, body):
        with self.db() as db:
            return self._event(db, job_id, kind, body)

    def job(self, job_id, *, include_deleted=False):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or (row["deleted"] and not include_deleted):
                raise KeyError("work_order_not_found")
            result = dict(row)
            result['budget'] = json.loads(result['budget'])
            return result

    def jobs(self):
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM jobs WHERE deleted=0 ORDER BY queue_seq,seq")]
        position = 0
        for row in rows:
            row['budget'] = json.loads(row['budget'])
            if row["state"] == "queued":
                position += 1
                row["position"] = position
            else:
                row["position"] = None
        return rows

    def update(self, job_id, **fields):
        valid = {"state", "revision", "plan_version", "plan", "confirmed_version", "thread_id", "turn_id",
                 "model", "reasoning_effort", "tokens", "token_cap", "usage_known", "pending_text", "resume_state", "reason", "deleted", "queue_seq", "title", "brief", "visibility", "budget"}
        if set(fields) - valid:
            raise ValueError("invalid_job_field")
        fields["updated_at"] = now()
        if 'budget' in fields:
            fields['budget'] = json.dumps(fields['budget'], ensure_ascii=False)
        with self.db() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", (*fields.values(), job_id))
        return self.job(job_id, include_deleted=True)

    def message(self, job_id, role, body):
        if not isinstance(body, str) or not body.strip() or len(body) > 30000:
            raise ValueError("invalid_message")
        with self.db() as db:
            cur = db.execute("INSERT INTO messages(job_id,role,body,created_at) VALUES(?,?,?,?)", (job_id, role, body, now()))
            return cur.lastrowid

    def consume_input(self, job_id, sent_text):
        """Remove only the exact prefix delivered to the turn, retaining edits
        received while thread/start or turn/start was awaiting a response."""
        with self.db() as db:
            pending = db.execute('SELECT pending_text FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
            if pending == sent_text:
                rest = ''
            elif sent_text and pending.startswith(sent_text + '\n'):
                rest = pending[len(sent_text)+1:]
            else:
                return
            db.execute('UPDATE jobs SET pending_text=? WHERE id=?', (rest, job_id))

    def room(self, job_id, offset=0, limit=50):
        self.job(job_id)
        with self.db() as db:
            messages = [dict(row) for row in db.execute("SELECT * FROM messages WHERE job_id=? ORDER BY seq LIMIT ? OFFSET ?", (job_id, min(max(limit, 1), 100), max(offset, 0)))]
            artifacts = [dict(row) for row in db.execute("SELECT * FROM artifacts WHERE job_id=? ORDER BY created_at", (job_id,))]
            receipts = [dict(row) for row in db.execute("SELECT * FROM receipts WHERE job_id=? ORDER BY seq DESC LIMIT 50", (job_id,))]
            total = db.execute("SELECT count(*) FROM messages WHERE job_id=?", (job_id,)).fetchone()[0]
        for receipt in receipts:
            receipt["body"] = json.loads(receipt["body"])
        from .quality import latest
        for artifact in artifacts:
            artifact['quality'] = latest(self, artifact['id'])
        job = self.job(job_id)
        from .budget import summary
        job['budget_summary'] = summary(job, limits_enabled=self.settings()['budget_limits_enabled'])
        from .account_meter import summary as meter_summary
        job['account_usage']=meter_summary(self,job_id)
        from .decisions import pending
        return {"job": job, "messages": messages, "total_messages": total, "artifacts": artifacts, "receipts": receipts,
                "pending_decisions":pending(self,job_id)}

    def create(self, title, brief, key, visibility='public'):
        if visibility not in ('public', 'private'):
            raise ValueError('invalid_visibility')
        if not key or not isinstance(title, str) or not title.strip() or len(title) > 120 or not isinstance(brief, str) or not brief.strip() or len(brief) > 30000:
            raise ValueError("invalid_work_order")
        key = 'create:' + key
        with self.db() as db:
            old = db.execute("SELECT result FROM commands WHERE key=?", (key,)).fetchone()
            if old:
                return json.loads(old[0])
            job_id = uuid.uuid4().hex
            seq = db.execute("SELECT coalesce(max(queue_seq),0)+1 FROM jobs").fetchone()[0]
            db.execute("INSERT INTO jobs(id,title,brief,state,queue_seq,pending_text,created_at,updated_at,visibility) VALUES(?,?,?,'queued',?,?,?,?,?)", (job_id, title.strip(), brief, seq, brief, now(), now(), visibility))
            message_seq = db.execute("INSERT INTO messages(job_id,role,body,created_at) VALUES(?,?,?,?)", (job_id, "k", brief, now())).lastrowid
            from .action_evidence import k_action
            self._event(db, job_id, "accepted", {"title": title, "brief": brief, "queue_seq": seq, "visibility": visibility,
                **k_action('create', 1, message_seq=message_seq)})
            result = {"id": job_id, "queue_seq": seq}
            db.execute("INSERT INTO commands VALUES(?,?)", (key, json.dumps(result)))
            return result

    def claim(self, cfg, exclude_job_ids=()):
        with self.db() as db:
            active = {row[0] for row in db.execute("SELECT id FROM jobs WHERE deleted=0 AND state IN ('discussing','executing','stopping')")}
            active.update(row[0] for row in db.execute("SELECT job_id FROM quality_reviews WHERE status='running'"))
            active.update(exclude_job_ids)
            if len(active) >= cfg['max_parallel']:
                return None
            rows = db.execute("SELECT * FROM jobs WHERE deleted=0 AND state='queued' ORDER BY queue_seq,seq").fetchall()
            calibrating = {item['id'] for item in db.execute("SELECT id,budget FROM jobs WHERE deleted=0") if json.loads(item['budget']).get('mode') == 'calibrating'} if cfg['budget_limits_enabled'] else set()
            row = next((item for item in rows if item['id'] not in active and (not calibrating or item['id'] in calibrating)), None)
            if not row:
                return None
            stage = "executing" if row["confirmed_version"] and row["confirmed_version"] == row["plan_version"] else "discussing"
            from .budget import initialize
            job = dict(row)
            job['budget'] = json.loads(job['budget'])
            if cfg['budget_limits_enabled']:
                if calibrating and (active or job['id'] not in calibrating):
                    return None
            import time
            if cfg['budget_limits_enabled'] and job['budget'].get('baseline_pending') and time.time() < job['budget']['not_before']:
                return None
            budget, cap = initialize(db, job, cfg) if cfg['budget_limits_enabled'] else (job['budget'],job['token_cap'])
            if not cap and cfg['budget_limits_enabled']:
                db.execute("UPDATE jobs SET state='budget_paused',reason='calibration_required',model=CASE WHEN model='' THEN ? ELSE model END,budget=?,updated_at=? WHERE id=?",
                           (cfg['model'], json.dumps(budget), now(), row['id']))
                self._event(db, row['id'], 'budget_paused', {'reason':'calibration_required', 'summary':'此模型尚无独立百分比参考，请在设置手动批准一次校准；工单已保存。'})
                return None
            db.execute("UPDATE jobs SET state=?,model=CASE WHEN model='' THEN ? ELSE model END,token_cap=?,budget=?,updated_at=? WHERE id=?", (stage, cfg["model"], cap, json.dumps(budget), now(), row["id"]))
            result = dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
            result['budget'] = json.loads(result['budget'])
            return result

    def recover(self):
        with self.db() as db:
            rows = list(db.execute("SELECT id,state FROM jobs WHERE state IN ('executing','discussing','stopping')"))
            for row in rows:
                db.execute("UPDATE jobs SET state='recovery_required',resume_state=?,reason='进程重启；需要核对线程与现有文件后恢复' WHERE id=?", (row["state"], row["id"]))
                self._event(db, row["id"], "recovery_required", {"previous_state": row["state"]})

    def record_usage(self, job_id, thread_id, total):
        if type(total) is not int or total < 0:
            raise ValueError("invalid_usage")
        with self.db() as db:
            old = db.execute("SELECT watermark FROM usage WHERE thread_id=?", (thread_id,)).fetchone()
            watermark = old[0] if old else 0
            if total < watermark:
                db.execute("UPDATE jobs SET usage_known=0 WHERE id=?", (job_id,))
                return False
            db.execute("INSERT INTO usage VALUES(?,?) ON CONFLICT(thread_id) DO UPDATE SET watermark=excluded.watermark", (thread_id, total))
            db.execute("UPDATE jobs SET tokens=tokens+?,usage_known=1 WHERE id=?", (total-watermark, job_id))
            return True

    def pending_events(self, *, projection=False, kinds=None):
        field = "projected" if projection else "consumed"
        selected = tuple(kinds) if kinds is not None else None
        if selected == ():
            return []
        condition = "" if selected is None else " AND kind IN (" + ",".join("?" for _ in selected) + ")"
        with self.db() as db:
            return [{**dict(row), "body": json.loads(row["body"])} for row in db.execute(f"SELECT * FROM receipts WHERE {field}=0{condition} ORDER BY seq LIMIT 100", selected or ())]

    def acknowledge(self, ids, *, projection=False):
        field = "projected" if projection else "consumed"
        with self.db() as db:
            db.executemany(f"UPDATE receipts SET {field}=1 WHERE id=?", [(item,) for item in ids])
