"""
同步操作管理 — 失败重试 + 聊天提示注入

表: pending_sync
- 记录失败的采集操作
- 支持重试
- 成功后清除同类型所有 pending
"""
import logging
import sqlite3
import threading
import os
import uuid as _uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "silicon_perception", "data")
DB_PATH = os.path.join(DB_DIR, "silicon_perception.db")  # merged from health_tracker.db

# 聊天注入回调（由 main.py 设置）
_chat_injector = None  # async fn(msg: str, is_recheck: bool) -> None


def set_chat_injector(injector):
    global _chat_injector
    _chat_injector = injector


class SyncManager:
    """同步操作管理器"""

    def __init__(self):
        os.makedirs(DB_DIR, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_sync (
                        id TEXT PRIMARY KEY,
                        sync_type TEXT NOT NULL,
                        triggered_at TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT,
                        active_date TEXT NOT NULL,
                        injected_msg_id TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ps_active ON pending_sync(status, active_date)")
                conn.commit()

    def create(self, sync_type: str, error: str) -> Optional[str]:
        """创建一条待同步记录。返回记录 ID。"""
        sync_id = str(_uuid.uuid4())[:8]
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%S")
        active_date = now.strftime("%Y-%m-%d")

        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                # 如果已有同类型 pending，更新错误信息而不是新增
                existing = conn.execute(
                    "SELECT id, retry_count FROM pending_sync WHERE sync_type=? AND status='pending' AND active_date=?",
                    (sync_type, active_date)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE pending_sync SET last_error=?, retry_count=retry_count+1, triggered_at=? WHERE id=?",
                        (error, timestamp, existing[0])
                    )
                    conn.commit()
                    return existing[0]

                conn.execute(
                    "INSERT INTO pending_sync (id, sync_type, triggered_at, status, last_error, active_date) "
                    "VALUES (?, ?, ?, 'pending', ?, ?)",
                    (sync_id, sync_type, timestamp, error, active_date)
                )
                conn.commit()
                return sync_id

    def mark_done(self, sync_type: str):
        """标记同类型所有 pending 为 done。"""
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "UPDATE pending_sync SET status='done' WHERE sync_type=? AND status='pending'",
                    (sync_type,)
                )
                conn.commit()

    def get_pending(self, active_date: str = None) -> List[Dict]:
        """获取所有未完成的同步记录。"""
        if active_date is None:
            active_date = datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM pending_sync WHERE status='pending' AND active_date=? ORDER BY triggered_at DESC",
                    (active_date,)
                ).fetchall()
                return [dict(r) for r in rows]

    def get_pending_types(self) -> List[str]:
        """获取当前 pending 的数据类型列表（用于前端判断哪些按钮显示重试标记）。"""
        pendings = self.get_pending()
        return list(set(p["sync_type"] for p in pendings))

    async def inject_failure_notice(self, sync_type: str, error: str):
        """注入失败提示到聊天。"""
        global _chat_injector
        if not _chat_injector:
            logger.warning("[SyncManager] chat_injector 未设置，无法注入提示")
            return

        type_labels = {"sleep": "睡眠数据", "spo2": "血氧数据", "steps": "步数数据"}
        label = type_labels.get(sync_type, sync_type)

        msg = (
            f"⚠️ 系统提示：{label}同步失败\n"
            f"原因：{error}\n"
            f"点击 🔄 重试"
        )

        try:
            await _chat_injector(msg, is_recheck=True)
            logger.info(f"[SyncManager] 失败提示已注入: {sync_type}")
        except Exception as e:
            logger.error(f"[SyncManager] 注入失败: {e}")

    async def on_capture_failed(self, sync_type: str, error: str):
        """采集失败时的统一处理：记录 + 注入提示。"""
        self.create(sync_type, error)
        await self.inject_failure_notice(sync_type, error)

    async def on_capture_success(self, sync_type: str):
        """采集成功时清除 pending。"""
        self.mark_done(sync_type)
        # 成功后注清除提示（聊天 reload 会自动隐藏已解决的提示）
        logger.info(f"[SyncManager] {sync_type} 采集成功，pending 已清除")


# 模块级单例
_instance: Optional[SyncManager] = None


def get_sync_manager() -> SyncManager:
    global _instance
    if _instance is None:
        _instance = SyncManager()
    return _instance
