from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .policy import MAX_ANALYSIS_LAG_SECONDS, MAX_CONTACT_MATERIAL_AGE_SECONDS


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "events" / "ambient_listening.db"
AUDIO_DIR = BASE_DIR / "data" / "ambient_audio"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AmbientStore:
    """Small local authority for expiring audio and bounded local diagnostics."""

    def __init__(self, db_path: Path = DB_PATH, audio_dir: Path = AUDIO_DIR):
        self.db_path = Path(db_path)
        self.audio_dir = Path(audio_dir)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS segments (
                    clip_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'received',
                    transcript TEXT NOT NULL DEFAULT '',
                    prosody_json TEXT NOT NULL DEFAULT '{}',
                    audio_context_json TEXT NOT NULL DEFAULT '{}',
                    reserved_batch TEXT NOT NULL DEFAULT '',
                    consumed INTEGER NOT NULL DEFAULT 0,
                    consumed_reason TEXT NOT NULL DEFAULT '',
                    message_relation TEXT NOT NULL DEFAULT '',
                    linked_message_id TEXT NOT NULL DEFAULT '',
                    relation_score REAL NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ambient_segments_state
                    ON segments(state, consumed, captured_at);
                CREATE INDEX IF NOT EXISTS idx_ambient_segments_expiry
                    ON segments(expires_at);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    next_action TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ambient_checkpoints_at
                    ON checkpoints(at DESC);
                CREATE TABLE IF NOT EXISTS speaker_bindings (
                    speaker_key TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    participate INTEGER NOT NULL,
                    event_description TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    paper_note TEXT NOT NULL DEFAULT '',
                    material_count INTEGER NOT NULL DEFAULT 0,
                    material_chars INTEGER NOT NULL DEFAULT 0,
                    device_background_json TEXT NOT NULL DEFAULT '[]',
                    outcome TEXT NOT NULL DEFAULT 'judged',
                    event_id TEXT NOT NULL DEFAULT '',
                    message_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ambient_decisions_at
                    ON decisions(at DESC);
                CREATE INDEX IF NOT EXISTS idx_ambient_decisions_expiry
                    ON decisions(expires_at);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(segments)").fetchall()}
            if "audio_context_json" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN audio_context_json TEXT NOT NULL DEFAULT '{}'")
            if "consumed_reason" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN consumed_reason TEXT NOT NULL DEFAULT ''")
            if "message_relation" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN message_relation TEXT NOT NULL DEFAULT ''")
            if "linked_message_id" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN linked_message_id TEXT NOT NULL DEFAULT ''")
            if "relation_score" not in columns:
                conn.execute("ALTER TABLE segments ADD COLUMN relation_score REAL NOT NULL DEFAULT 0")

    def checkpoint(self, correlation_id: str, stage: str, status: str,
                   error_code: str = "", retryable: bool = False,
                   next_action: str = "") -> None:
        # Deliberately exclude transcript/audio/body text from diagnostics.
        safe = lambda value, cap: str(value or "").replace("\n", " ")[:cap]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO checkpoints(at,correlation_id,stage,status,error_code,retryable,next_action) "
                "VALUES(?,?,?,?,?,?,?)",
                (utc_now(), safe(correlation_id, 96), safe(stage, 48), safe(status, 24),
                 safe(error_code, 80), 1 if retryable else 0, safe(next_action, 120)),
            )
            # Source evidence must survive high-frequency mobile config receipts.
            source_stages = "('source_selection','source_input_health')"
            for predicate, limit in ((f"stage IN {source_stages}", 1600),
                                     (f"stage NOT IN {source_stages}", 800)):
                conn.execute(
                    f"DELETE FROM checkpoints WHERE {predicate} AND id NOT IN "
                    f"(SELECT id FROM checkpoints WHERE {predicate} ORDER BY id DESC LIMIT ?)",
                    (limit,),
                )
            conn.execute("DELETE FROM checkpoints WHERE at<?",
                         ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),))

    def save_segment(self, *, clip_id: str, correlation_id: str, captured_at: str,
                     expires_at: str, audio_bytes: bytes,
                     audio_context: dict[str, Any] | None = None) -> tuple[bool, str]:
        """Idempotently persist one WAV. Returns (created, path)."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT file_path FROM segments WHERE clip_id=?", (clip_id,)
            ).fetchone()
            if existing:
                return False, str(existing["file_path"])
            target = self.audio_dir / f"{clip_id}.wav"
            part = target.with_suffix(".wav.part")
            with open(part, "wb") as handle:
                handle.write(audio_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(part, target)
            try:
                conn.execute(
                    "INSERT INTO segments(clip_id,correlation_id,captured_at,expires_at,received_at,file_path,audio_context_json) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (clip_id, correlation_id, captured_at, expires_at, utc_now(), str(target),
                     json.dumps(audio_context or {}, ensure_ascii=False)),
                )
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return True, str(target)

    def get_segment(self, clip_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM segments WHERE clip_id=?", (clip_id,)).fetchone()
            return dict(row) if row else None

    def pending_segments(self, limit: int = 20,
                         max_age_seconds: int | None = None,
                         newest_first: bool = False) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat() \
            if max_age_seconds is not None else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM segments WHERE state IN ('received','retry') AND expires_at>? "
                "AND captured_at>=? ORDER BY captured_at "
                + ("DESC" if newest_first else "ASC") + " LIMIT ?",
                (utc_now(), cutoff, int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]

    def finish_analysis(self, clip_id: str, transcript: str, prosody: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE segments SET state='ready', transcript=?, prosody_json=?, error_code='' "
                "WHERE clip_id=?",
                (str(transcript or ""), json.dumps(prosody or {}, ensure_ascii=False), clip_id),
            )

    def skip_non_speech(self, clip_id: str, gate: dict[str, Any]) -> None:
        """A terminal analysis result, not a deletion or a fabricated transcript."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE segments SET state='skipped', consumed=1, "
                "consumed_reason='no_speech_detected', prosody_json=?, error_code='' "
                "WHERE clip_id=? AND state IN ('received','retry')",
                (json.dumps({"speech_gate": gate}, ensure_ascii=False), clip_id),
            )

    def mark_message_relation(self, clip_ids: list[str], *, relation: str,
                              message_id: str, score: float, consume: bool) -> int:
        """Link ambient material to an authoritative user message without deleting raw audio."""
        if not clip_ids:
            return 0
        marks = ",".join("?" for _ in clip_ids)
        safe_relation = str(relation or "")[:48]
        safe_message_id = str(message_id or "")[:160]
        safe_score = max(0.0, min(float(score or 0.0), 1.0))
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE segments SET message_relation=?, linked_message_id=?, relation_score=?, "
                f"consumed=CASE WHEN ? THEN 1 ELSE consumed END, "
                f"consumed_reason=CASE WHEN ? THEN ? ELSE consumed_reason END, reserved_batch='' "
                f"WHERE clip_id IN ({marks}) AND state='ready' AND consumed=0",
                (safe_relation, safe_message_id, safe_score, int(bool(consume)),
                 int(bool(consume)), safe_relation, *clip_ids),
            )
            return int(cursor.rowcount or 0)

    def fail_analysis(self, clip_id: str, error_code: str, retry: bool) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE segments SET state=?, error_code=? WHERE clip_id=?",
                ("retry" if retry else "failed", str(error_code or "")[:80], clip_id),
            )

    def ready_material(self, limit: int = 80,
                       max_age_seconds: int | None = None) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat() \
            if max_age_seconds is not None else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM segments WHERE state='ready' AND consumed=0 "
                "AND reserved_batch='' AND expires_at>? AND captured_at>=? "
                "ORDER BY captured_at LIMIT ?",
                (utc_now(), cutoff, int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]

    def reserve(self, clip_ids: list[str], batch_id: str) -> None:
        if not clip_ids:
            return
        marks = ",".join("?" for _ in clip_ids)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE segments SET reserved_batch=? WHERE clip_id IN ({marks}) "
                "AND consumed=0 AND reserved_batch=''",
                (batch_id, *clip_ids),
            )

    def release(self, batch_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE segments SET reserved_batch='' WHERE reserved_batch=?", (batch_id,))

    def consume(self, batch_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE segments SET consumed=1, reserved_batch='' WHERE reserved_batch=?",
                (batch_id,),
            )

    def record_decision(self, *, decision_id: str, batch_id: str,
                        correlation_id: str, expires_at: str,
                        participate: bool, event_description: str,
                        summary: str, reason: str, paper_note: str,
                        material_count: int, material_chars: int,
                        device_background: list[dict[str, Any]],
                        outcome: str) -> None:
        """Persist Flash's explicit result, not its prompt or hidden reasoning."""
        safe = lambda value, cap: str(value or "").replace("\x00", "")[:cap]
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO decisions("
                "decision_id,batch_id,correlation_id,at,expires_at,participate,"
                "event_description,summary,reason,paper_note,material_count,material_chars,"
                "device_background_json,outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    safe(decision_id, 96), safe(batch_id, 96), safe(correlation_id, 96),
                    utc_now(), safe(expires_at, 48), 1 if participate else 0,
                    safe(event_description, 180), safe(summary, 500), safe(reason, 240),
                    safe(paper_note, 80), max(0, int(material_count)),
                    max(0, int(material_chars)),
                    json.dumps(device_background or [], ensure_ascii=False)[:12000],
                    safe(outcome, 64),
                ),
            )

    def update_decision(self, decision_id: str, *, outcome: str,
                        event_id: str = "", message_id: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE decisions SET outcome=?, "
                "event_id=CASE WHEN ?<>'' THEN ? ELSE event_id END, "
                "message_id=CASE WHEN ?<>'' THEN ? ELSE message_id END "
                "WHERE decision_id=?",
                (
                    str(outcome or "")[:64],
                    str(event_id or "")[:160], str(event_id or "")[:160],
                    str(message_id or "")[:160], str(message_id or "")[:160],
                    str(decision_id or "")[:96],
                ),
            )

    @staticmethod
    def _decoded_json(value: Any, fallback: Any) -> Any:
        try:
            decoded = json.loads(str(value or ""))
            return decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    def monitor_records(self, limit: int = 80) -> dict[str, list[dict[str, Any]]]:
        """Return an explicit, expiring UI projection without local file paths."""
        capped = max(1, min(int(limit), 200))
        now = utc_now()
        analysis_cutoff = (datetime.now(timezone.utc) -
                           timedelta(seconds=MAX_ANALYSIS_LAG_SECONDS)).isoformat()
        contact_cutoff = (datetime.now(timezone.utc) -
                          timedelta(seconds=MAX_CONTACT_MATERIAL_AGE_SECONDS)).isoformat()
        with self._lock, self._connect() as conn:
            materials = conn.execute(
                "SELECT clip_id,correlation_id,captured_at,expires_at,state,transcript,"
                "audio_context_json,prosody_json,consumed,consumed_reason,message_relation,"
                "linked_message_id,relation_score,error_code,file_path "
                "FROM segments WHERE expires_at>? ORDER BY captured_at DESC LIMIT ?",
                (now, capped),
            ).fetchall()
            decisions = conn.execute(
                "SELECT decision_id,batch_id,correlation_id,at,expires_at,participate,"
                "event_description,summary,reason,paper_note,material_count,material_chars,"
                "device_background_json,outcome,event_id,message_id "
                "FROM decisions WHERE expires_at>? ORDER BY at DESC LIMIT ?",
                (now, capped),
            ).fetchall()
        material_items = []
        for source in materials:
            row = dict(source)
            file_path = row.pop("file_path", "")
            row["has_audio"] = bool(file_path and Path(file_path).is_file())
            row["consumed"] = bool(row.get("consumed"))
            row["audio_context"] = self._decoded_json(row.pop("audio_context_json", "{}"), {})
            analysis = self._decoded_json(row.pop("prosody_json", "{}"), {})
            row["speaker"] = analysis.get("speaker", {})
            row["speech_gate"] = analysis.get("speech_gate", {})
            if row["state"] in {"received", "retry"} and row["captured_at"] < analysis_cutoff:
                row["state"] = "stale"
                row["consumed_reason"] = "stale_before_analysis"
            elif row["state"] == "ready" and not row["consumed"] and row["captured_at"] < contact_cutoff:
                row["consumed_reason"] = "stale_before_judgment"
            material_items.append(row)
        decision_items = []
        for source in decisions:
            row = dict(source)
            row["participate"] = bool(row.get("participate"))
            row["device_background"] = self._decoded_json(
                row.pop("device_background_json", "[]"), []
            )
            decision_items.append(row)
        return {"materials": material_items, "decisions": decision_items}

    def cleanup_expired(self, now_iso: str | None = None) -> int:
        now_iso = now_iso or utc_now()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT clip_id,file_path FROM segments WHERE expires_at<=?", (now_iso,)
            ).fetchall()
            for row in rows:
                try:
                    Path(row["file_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            conn.execute("DELETE FROM segments WHERE expires_at<=?", (now_iso,))
            conn.execute("DELETE FROM decisions WHERE expires_at<=?", (now_iso,))
            return len(rows)

    def status(self) -> dict[str, Any]:
        now = utc_now()
        analysis_cutoff = (datetime.now(timezone.utc) -
                           timedelta(seconds=MAX_ANALYSIS_LAG_SECONDS)).isoformat()
        with self._lock, self._connect() as conn:
            counts = {row["state"]: int(row["n"]) for row in conn.execute(
                "SELECT state,COUNT(*) AS n FROM segments WHERE expires_at>? GROUP BY state",
                (now,),
            ).fetchall()}
            oldest_pending = conn.execute(
                "SELECT MIN(captured_at) FROM segments "
                "WHERE state IN ('received','retry') AND expires_at>? AND captured_at>=?",
                (now, analysis_cutoff),
            ).fetchone()[0]
            stale_count = int(conn.execute(
                "SELECT COUNT(*) FROM segments WHERE state IN ('received','retry') "
                "AND expires_at>? AND captured_at<?",
                (now, analysis_cutoff),
            ).fetchone()[0])
            latest = conn.execute(
                "SELECT at,correlation_id,stage,status,error_code,retryable,next_action "
                "FROM checkpoints ORDER BY id DESC LIMIT 1"
            ).fetchone()
            decision_count = int(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE expires_at>?", (now,)
            ).fetchone()[0])
            latest_material = conn.execute(
                "SELECT MAX(captured_at), MAX(CASE WHEN state='ready' AND transcript<>'' "
                "THEN captured_at END) FROM segments WHERE expires_at>?", (now,)
            ).fetchone()
            try:
                pending_age = max(0, int((datetime.fromisoformat(now) -
                                          datetime.fromisoformat(oldest_pending)).total_seconds())) \
                    if oldest_pending else 0
            except ValueError:
                pending_age = 0
            return {"segments": counts, "decisions": decision_count,
                    "latest_capture_at": latest_material[0],
                    "latest_transcribed_capture_at": latest_material[1],
                    "speech_skipped_count": counts.get("skipped", 0),
                    "pending_count": counts.get("received", 0) + counts.get("retry", 0) - stale_count,
                    "stale_unprocessed_count": stale_count,
                    "oldest_pending_age_seconds": pending_age,
                    "latest_checkpoint": dict(latest) if latest else None}

    def checkpoints(self, limit: int = 80) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT at,correlation_id,stage,status,error_code,retryable,next_action "
                "FROM checkpoints ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 200)),)
            ).fetchall()
            return [dict(row) for row in rows]

    def bindings(self) -> list[dict[str, str]]:
        with self._lock, self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT speaker_key,entity_id,label,updated_at FROM speaker_bindings ORDER BY updated_at DESC"
            ).fetchall()]

    def bind(self, speaker_key: str, entity_id: str, label: str = "") -> dict[str, str]:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO speaker_bindings(speaker_key,entity_id,label,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(speaker_key) DO UPDATE SET entity_id=excluded.entity_id,"
                "label=excluded.label,updated_at=excluded.updated_at",
                (speaker_key, entity_id, label[:80], now),
            )
        return {"speaker_key": speaker_key, "entity_id": entity_id, "label": label[:80], "updated_at": now}


_store: AmbientStore | None = None


def get_ambient_store() -> AmbientStore:
    global _store
    if _store is None:
        _store = AmbientStore()
    return _store
