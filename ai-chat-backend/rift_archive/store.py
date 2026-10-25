"""Persistent Rift Archive state with DPAPI-protected per-game tokens."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Protocol


class SecretProtector(Protocol):
    def protect(self, value: str) -> bytes: ...
    def unprotect(self, value: bytes) -> str: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class DpapiProtector:
    """Bind game credentials to the current Windows account."""

    _entropy = b"MIRROW:RiftArchive:v1"

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buf = ctypes.create_string_buffer(data)
        return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    def protect(self, value: str) -> bytes:
        if not hasattr(ctypes, "windll"):
            raise RuntimeError("rift_credentials_require_windows_dpapi")
        raw, raw_buf = self._blob(value.encode("utf-8"))
        entropy, entropy_buf = self._blob(self._entropy)
        out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(raw), "MIRROW Rift Archive", ctypes.byref(entropy), None, None,
            0x01, ctypes.byref(out),
        )
        _ = raw_buf, entropy_buf
        if not ok:
            raise ctypes.WinError()
        try:
            encrypted = ctypes.string_at(out.pbData, out.cbData)
            return base64.b64encode(encrypted)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def unprotect(self, value: bytes) -> str:
        if not hasattr(ctypes, "windll"):
            raise RuntimeError("rift_credentials_require_windows_dpapi")
        encrypted = base64.b64decode(value)
        raw, raw_buf = self._blob(encrypted)
        entropy, entropy_buf = self._blob(self._entropy)
        out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(raw), None, ctypes.byref(entropy), None, None,
            0x01, ctypes.byref(out),
        )
        _ = raw_buf, entropy_buf
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)


class RiftArchiveStore:
    def __init__(self, db_path: str | Path | None = None, *, protector: SecretProtector | None = None):
        self.db_path = Path(db_path or Path(__file__).parents[1] / "events" / "rift_archive.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DpapiProtector()
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
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
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS rift_games (
                    game_id TEXT PRIMARY KEY,
                    token_cipher BLOB NOT NULL,
                    case_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    opening TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    selected INTEGER NOT NULL DEFAULT 0,
                    play_mode INTEGER NOT NULL DEFAULT 0,
                    interaction_mode TEXT NOT NULL DEFAULT 'partner',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rift_games_selected ON rift_games(selected);
                CREATE TABLE IF NOT EXISTS rift_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT '',
                    action_id TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL,
                    state_hash TEXT NOT NULL DEFAULT '',
                    UNIQUE(game_id, state_hash, action_id)
                );
                CREATE TABLE IF NOT EXISTS rift_start_proposal (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    case_id TEXT NOT NULL,
                    opening TEXT NOT NULL,
                    proposed_at TEXT NOT NULL
                );
            """)

    @staticmethod
    def _row(row: sqlite3.Row | None, *, token: str = "") -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("token_cipher", None)
        try:
            result["snapshot"] = json.loads(result.pop("snapshot_json") or "{}")
        except (ValueError, json.JSONDecodeError):
            result["snapshot"] = {}
        result["selected"] = bool(result.get("selected"))
        result["play_mode"] = bool(result.get("play_mode"))
        if token:
            result["game_token"] = token
        return result

    def upsert_game(self, *, game_id: str, game_token: str, case_id: str = "", title: str = "",
                    opening: str = "", status: str = "active", snapshot: dict[str, Any] | None = None,
                    snapshot_at: str = "", interaction_mode: str = "partner", now: str) -> dict[str, Any]:
        cipher = self.protector.protect(game_token)
        snap = json.dumps(snapshot or {}, ensure_ascii=False, separators=(",", ":"))
        if len(snap) > 524288:
            raise ValueError("rift_snapshot_too_large")
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE rift_games SET selected=0")
            conn.execute("""
                INSERT INTO rift_games
                    (game_id, token_cipher, case_id, title, opening, status, selected, play_mode,
                     interaction_mode, snapshot_json, snapshot_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    token_cipher=excluded.token_cipher, case_id=excluded.case_id,
                    title=excluded.title, opening=excluded.opening, status=excluded.status,
                    selected=1, interaction_mode=excluded.interaction_mode,
                    snapshot_json=excluded.snapshot_json, snapshot_at=excluded.snapshot_at,
                    updated_at=excluded.updated_at
            """, (game_id, cipher, case_id, title, opening, status, interaction_mode,
                  snap, snapshot_at, now, now))
            row = conn.execute("SELECT * FROM rift_games WHERE game_id=?", (game_id,)).fetchone()
        return self._row(row) or {}

    def get_selected(self, *, include_token: bool = False) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rift_games WHERE selected=1 ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        token = self.protector.unprotect(row["token_cipher"]) if row and include_token else ""
        return self._row(row, token=token)

    def update_snapshot(self, game_id: str, snapshot: dict[str, Any], *, status: str,
                        title: str, case_id: str, snapshot_at: str, now: str) -> None:
        snap = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        if len(snap) > 524288:
            raise ValueError("rift_snapshot_too_large")
        with self._lock, self._connect() as conn:
            conn.execute("""
                UPDATE rift_games SET snapshot_json=?, snapshot_at=?, status=?,
                    title=CASE WHEN ?<>'' THEN ? ELSE title END,
                    case_id=CASE WHEN ?<>'' THEN ? ELSE case_id END,
                    updated_at=? WHERE game_id=?
            """, (snap, snapshot_at, status, title, title, case_id, case_id, now, game_id))

    def set_play_mode(self, enabled: bool) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE rift_games SET play_mode=? WHERE selected=1", (1 if enabled else 0,))

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in {"partner", "consult"}:
            raise ValueError("invalid_interaction_mode")
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE rift_games SET interaction_mode=? WHERE selected=1", (mode,))

    def disconnect(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE rift_games SET selected=0, play_mode=0 WHERE selected=1")

    def set_start_proposal(self, *, case_id: str, opening: str, proposed_at: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO rift_start_proposal(singleton, case_id, opening, proposed_at)
                VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    case_id=excluded.case_id, opening=excluded.opening,
                    proposed_at=excluded.proposed_at
            """, (case_id, opening, proposed_at))

    def get_start_proposal(self) -> dict[str, str] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT case_id, opening, proposed_at FROM rift_start_proposal WHERE singleton=1"
            ).fetchone()
        return dict(row) if row else None

    def clear_start_proposal(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM rift_start_proposal WHERE singleton=1")

    def append_event(self, *, game_id: str, actor: str, action_id: str, summary: str,
                     observed_at: str, state_hash: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO rift_events
                    (game_id, actor, action_id, summary, observed_at, state_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (game_id, actor, action_id, summary[:1000], observed_at, state_hash))

    def recent_events(self, game_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("""
                SELECT actor, action_id, summary, observed_at FROM rift_events
                WHERE game_id=? ORDER BY id DESC LIMIT ?
            """, (game_id, max(1, min(limit, 20)))).fetchall()
        return [dict(row) for row in reversed(rows)]
