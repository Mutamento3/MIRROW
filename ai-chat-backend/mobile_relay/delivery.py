"""手机通知投递队列。

会话正文仍由现有消息存储负责；这里只保存通知标题/摘要和递送状态，
用于 WS 断线后的重试。单用户本地实例采用一个轻量 SQLite 文件即可。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import List, Optional


_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "mobile_relay.db")
_STALE_SENDING_SECONDS = 5 * 60
_MAX_BODY_LENGTH = 120


@dataclass(frozen=True)
class PendingDelivery:
    delivery_id: str
    title: str
    body: str
    attempts: int
    created_at: float


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mobile_delivery (
            delivery_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            next_attempt_at REAL NOT NULL,
            sent_at REAL,
            acked_at REAL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_delivery_pending "
        "ON mobile_delivery(status, next_attempt_at, created_at)"
    )
    conn.commit()
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def enqueue(title: str, body: str, delivery_id: Optional[str] = None) -> str:
    delivery_id = delivery_id or uuid.uuid4().hex
    now = time.time()
    safe_title = (title or "MIRROW")[:80]
    safe_body = (body or "AI 发来了一条消息")[:_MAX_BODY_LENGTH]
    with _connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mobile_delivery "
            "(delivery_id,title,body,status,attempts,created_at,next_attempt_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (delivery_id, safe_title, safe_body, "pending", 0, now, now),
        )
    return delivery_id


def claim_pending(limit: int = 20) -> List[PendingDelivery]:
    now = time.time()
    with _connection() as conn:
        # 进程崩溃后把遗留 sending 项重新放回待发送集合。
        conn.execute(
            "UPDATE mobile_delivery SET status='pending' "
            "WHERE status='sending' AND sent_at IS NULL AND next_attempt_at < ?",
            (now - _STALE_SENDING_SECONDS,),
        )
        rows = conn.execute(
            "SELECT delivery_id,title,body,attempts,created_at "
            "FROM mobile_delivery WHERE status='pending' AND next_attempt_at <= ? "
            "ORDER BY created_at ASC LIMIT ?",
            (now, max(1, min(limit, 100))),
        ).fetchall()
        ids = [row["delivery_id"] for row in rows]
        if ids:
            conn.executemany(
                "UPDATE mobile_delivery SET status='sending', attempts=attempts+1, sent_at=? "
                "WHERE delivery_id=? AND status='pending'",
                [(now, delivery_id) for delivery_id in ids],
            )
        return [
            PendingDelivery(
                delivery_id=row["delivery_id"],
                title=row["title"],
                body=row["body"],
                attempts=int(row["attempts"]) + 1,
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]


def mark_delivered(delivery_id: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE mobile_delivery SET status='delivered', acked_at=? WHERE delivery_id=?",
            (time.time(), delivery_id),
        )


def mark_failed(delivery_id: str, error: str = "delivery_failed") -> None:
    now = time.time()
    with _connection() as conn:
        row = conn.execute(
            "SELECT attempts FROM mobile_delivery WHERE delivery_id=?",
            (delivery_id,),
        ).fetchone()
        attempts = int(row["attempts"]) if row else 1
        # 退避上限30分钟；不会因为一次断线让后台任务高频轰炸手机。
        delay = min(30 * 60, max(15, 2 ** min(attempts, 6)))
        conn.execute(
            "UPDATE mobile_delivery SET status='pending', next_attempt_at=?, "
            "sent_at=NULL, last_error=? WHERE delivery_id=?",
            (now + delay, str(error or "delivery_failed")[:160], delivery_id),
        )


def stats() -> dict:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM mobile_delivery GROUP BY status"
        ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}
