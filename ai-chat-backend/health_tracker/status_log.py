"""
状态切换日志 — SQLite 持久化

表: status_change_log
- 用户每次切换状态时记录 (from_status → to_status, timestamp)
- 1分钟内重复切换：覆写同一条记录（更新 to_status + timestamp），不插入新行
- 按活跃日查询

使用: StatusChangeLog 是模块级单例，通过 get_status_log() 获取
"""
import sqlite3
import threading
import os
from datetime import datetime
from typing import Optional, List, Dict

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "silicon_perception", "data")
DB_PATH = os.path.join(DB_DIR, "silicon_perception.db")  # merged from health_tracker.db

_STATUS_DEBOUNCE_SECONDS = 120  # 2分钟内重复切换覆写（含网络延迟）


class StatusChangeLog:
    def __init__(self):
        os.makedirs(DB_DIR, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """DDL 权威来源：silicon_perception/recording/schema.py。
        此处仅做安全网兜底——schema 迁移未执行时自动建表。"""
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS status_change_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        from_status TEXT NOT NULL,
                        to_status TEXT NOT NULL,
                        active_date TEXT NOT NULL,
                        custom_text TEXT DEFAULT ''
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_scl_date ON status_change_log(active_date)")
                conn.commit()

    def record(self, from_status: str, to_status: str, custom_text: str = "") -> int:
        """
        记录状态切换。
        - 1分钟内反复切换：覆写同一条（保留原始 from_status，更新 to_status）
        - 1分钟后：固化。如果 from==to（误触纠正），丢弃；否则保留。
        返回记录的 id，丢弃时返回 -1。
        """
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")
        active_date = now.strftime("%Y-%m-%d")

        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                recent = conn.execute(
                    "SELECT id, from_status, to_status, timestamp FROM status_change_log ORDER BY id DESC LIMIT 1"
                ).fetchone()

                if recent:
                    recent_id, recent_from, recent_to, recent_ts = recent
                    try:
                        recent_dt = datetime.fromisoformat(recent_ts)
                        elapsed = (now - recent_dt).total_seconds()
                    except (ValueError, TypeError):
                        elapsed = 999

                    # 睡眠唤醒（>4h）跳过防抖——这是真正的睡醒，不是快速切换
                    is_wakeup = (
                        from_status in ("sleeping", "napping")
                        and to_status == "idle"
                        and recent_from in ("sleeping", "napping")
                        and elapsed > 14400  # 4h
                    )

                    if elapsed <= _STATUS_DEBOUNCE_SECONDS and not is_wakeup:
                        # 1分钟内 → 覆写（保留原始 from，更新 to）
                        new_to = to_status
                        # 如果回到原始状态（误触纠正），直接删除
                        if recent_from == new_to:
                            conn.execute("DELETE FROM status_change_log WHERE id=?", (recent_id,))
                            conn.commit()
                            return -1
                        conn.execute(
                            "UPDATE status_change_log SET to_status=?, timestamp=?, custom_text=? WHERE id=?",
                            (new_to, timestamp, custom_text, recent_id)
                        )
                        conn.commit()
                        return recent_id
                    else:
                        # 超过1分钟，先固化旧记录。如果旧记录 from==to，删除它
                        if recent_from == recent_to:
                            conn.execute("DELETE FROM status_change_log WHERE id=?", (recent_id,))
                            conn.commit()

                # 插入新行
                if from_status == to_status:
                    return -1  # 不会发生（已在入口过滤），保险
                cursor = conn.execute(
                    "INSERT INTO status_change_log (timestamp, from_status, to_status, active_date, custom_text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (timestamp, from_status, to_status, active_date, custom_text)
                )
                conn.commit()
                return cursor.lastrowid

    def get_log(self, active_date: str = None) -> List[Dict]:
        """按活跃日查询状态切换日志。不传则返回今天。"""
        if active_date is None:
            active_date = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM status_change_log WHERE active_date=? ORDER BY id ASC",
                    (active_date,)
                ).fetchall()
                return [dict(r) for r in rows]

    def get_current_status(self) -> Optional[Dict]:
        """获取最近一次状态切换记录。"""
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM status_change_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return dict(row) if row else None

    def get_transitions_between(self, active_date: str, from_status: str, to_status: str) -> List[Dict]:
        """查询特定状态切换记录（用于步数差值计算）。"""
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM status_change_log WHERE active_date=? "
                    "AND from_status=? AND to_status=? ORDER BY id ASC",
                    (active_date, from_status, to_status)
                ).fetchall()
                return [dict(r) for r in rows]

    def delete_logs_before(self, days: int = 30):
        """清理 N 天前的日志。"""
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        cutoff -= timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM status_change_log WHERE active_date < ?", (cutoff_str,))
                conn.commit()


# 模块级单例
_log_instance: Optional[StatusChangeLog] = None


def get_status_log() -> StatusChangeLog:
    global _log_instance
    if _log_instance is None:
        _log_instance = StatusChangeLog()
    return _log_instance
