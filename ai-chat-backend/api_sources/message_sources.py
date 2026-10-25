"""Immutable source receipts; contexts consume only the model-name projection."""
from __future__ import annotations
import json
import logging
from pathlib import Path
import sqlite3
from contextlib import closing
from .audit import DB_PATH

logger = logging.getLogger(__name__)


def sanitize(value) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("ordinal"), int) or value["ordinal"] < 1:
        return None
    keys = ("station_id", "station_name", "source_id", "model", "model_label")
    result = {key: str(value.get(key) or "")[:200] for key in keys}
    result["ordinal"] = value["ordinal"]
    return result


def save(session_id: str, message_id: str, value, path: Path = DB_PATH) -> None:
    value = sanitize(value)
    if not value or not session_id or not message_id:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=3)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS message_sources(session_id TEXT NOT NULL,message_id TEXT NOT NULL,source_json TEXT NOT NULL,PRIMARY KEY(session_id,message_id))")
            db.execute("INSERT OR IGNORE INTO message_sources VALUES(?,?,?)", (session_id, message_id, json.dumps(value, ensure_ascii=False)))
    except Exception:
        logger.warning("Message source receipt unavailable")


def for_session(session_id: str, path: Path = DB_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)) as db:
            return {key: sanitize(json.loads(value)) for key, value in db.execute("SELECT message_id,source_json FROM message_sources WHERE session_id=?", (session_id,))}
    except (sqlite3.Error, ValueError):
        return {}
