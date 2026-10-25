"""
记账本 - 核心管理器
存储: events/ledger.db SQLite
"""
import json
import sqlite3, os, uuid, logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from .models import LedgerEntry, LedgerStats

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "events" / "ledger.db"

def _get_ledger_ctx_cap() -> int:
    """获取记账本上下文捕获字符帽，0=无限制"""
    try:
        from truncation_config import get_truncation_limit
        cap = get_truncation_limit("ledger_ctx_cap")
        return cap if cap > 0 else 10**9  # 0=无限制
    except Exception:
        return 3000


class LedgerManager:
    """记账本管理器（单例）"""

    def __init__(self):
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(DB_PATH.parent, exist_ok=True)
            self._conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_table()
        return self._conn

    def _init_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_entries (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                debt_type TEXT NOT NULL DEFAULT 'promise_broken',
                source_context TEXT DEFAULT '',
                wander_event_id TEXT,
                created TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        # 新增 comment 列（兼容旧 DB）
        try:
            self._conn.execute(
                "ALTER TABLE ledger_entries ADD COLUMN comment TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass  # 列已存在
        # 新增 session_id 列（上下文恢复能力）
        try:
            self._conn.execute(
                "ALTER TABLE ledger_entries ADD COLUMN session_id TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        # 新增 last_active 列（衰减跟踪）
        try:
            self._conn.execute(
                "ALTER TABLE ledger_entries ADD COLUMN last_active TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        # 新增 settled_at 列（手动销账时间戳）
        try:
            self._conn.execute(
                "ALTER TABLE ledger_entries ADD COLUMN settled_at TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_status ON ledger_entries(status)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger_entries(debt_type)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_created ON ledger_entries(created)
        """)
        # 记忆上下文表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_memory (
                id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL REFERENCES ledger_entries(id),
                session_id TEXT NOT NULL,
                messages_snapshot TEXT NOT NULL,
                created TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_memory_entry ON ledger_memory(entry_id)
        """)
        self._conn.commit()

    def add(self, content: str, debt_type: str = "promise_broken",
            source_context: str = "", wander_event_id: str = None,
            session_id: str = "", before_context: list = None) -> LedgerEntry:
        """记一笔。可选存储对话上下文。返回创建的条目。"""
        conn = self._get_conn()
        entry_id = f"ledger_{uuid.uuid4().hex[:12]}"
        created = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO ledger_entries (id, content, debt_type, source_context, wander_event_id, created, status, session_id, last_active) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (entry_id, content, debt_type, source_context, wander_event_id, created, session_id or "", created)
        )
        # 存储对话上下文：session_id 有值但 before_context 为空时自动捕获
        if session_id:
            if before_context is None:
                before_context = self._capture_context(session_id)
                if before_context:
                    logger.info(f"Ledger 上下文自动捕获: session={session_id}, msgs={len(before_context)}")
            if before_context:
                self._store_memory_context(entry_id, session_id, before_context, created)
        conn.commit()
        return LedgerEntry(
            id=entry_id, content=content, debt_type=debt_type,
            source_context=source_context, wander_event_id=wander_event_id,
            created=created, status="active"
        )

    def _store_memory_context(self, entry_id: str, session_id: str,
                              messages: list, created: str):
        """存储账目关联的对话上下文。"""
        # 字符帽截断
        truncated = []
        total_chars = 0
        for msg in reversed(messages):
            msg_chars = len(msg.get("content", ""))
            if total_chars + msg_chars > _get_ledger_ctx_cap() and truncated:
                break
            truncated.insert(0, msg)
            total_chars += msg_chars
        memory_id = f"lmem_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO ledger_memory (id, entry_id, session_id, messages_snapshot, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_id, entry_id, session_id, json.dumps(truncated, ensure_ascii=False), created)
        )

    def _capture_context(self, session_id: str, char_limit: int = None) -> list:
        """从对话日志捕获最近消息作为上下文。失败返回空列表。"""
        if char_limit is None:
            char_limit = _get_ledger_ctx_cap()
        try:
            from event_chronicle import get_global_chronicle
            chronicle = get_global_chronicle()
            messages = chronicle.get_messages_by_session_id(session_id, limit=100)
            if not messages:
                return []
            # 字符帽截断（取最近消息）
            truncated = []
            total_chars = 0
            for msg in reversed(messages):
                msg_chars = len(msg.get("content", ""))
                if total_chars + msg_chars > char_limit and truncated:
                    break
                truncated.insert(0, msg)
                total_chars += msg_chars
            return truncated
        except Exception:
            return []

    def get_entries_by_wander_id(self, wander_event_id: str) -> List[LedgerEntry]:
        """按 wander_event_id 查询已有条目（用于去重）。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM ledger_entries WHERE wander_event_id=?",
            (wander_event_id,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_entry_with_context(self, entry_id: str) -> Optional[dict]:
        """获取单条账目 + 对话上下文 + 备注。已过期(dormant)条目也可查详情。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE id=? AND status IN ('active','dormant')",
            (entry_id,)
        ).fetchone()
        if not row:
            return None
        entry = self._row_to_entry(row)
        # 查询上下文
        mem_row = conn.execute(
            "SELECT messages_snapshot FROM ledger_memory WHERE entry_id=? ORDER BY created DESC LIMIT 1",
            (entry_id,)
        ).fetchone()
        context_msgs = []
        if mem_row:
            try:
                context_msgs = json.loads(mem_row["messages_snapshot"])
            except json.JSONDecodeError:
                pass
        # 回退恢复：ledger_memory 无记录但 entry 有 session_id 时，从 conversation_messages 重新捕获
        if not context_msgs and entry.session_id:
            logger.info(f"Ledger 上下文回退恢复: entry={entry_id}, session={entry.session_id}")
            try:
                from event_chronicle import get_global_chronicle
                chronicle = get_global_chronicle()
                msgs = chronicle.get_messages_by_session_id(entry.session_id, limit=100)
                if msgs:
                    cap = _get_ledger_ctx_cap()
                    truncated = []
                    total_chars = 0
                    for msg in reversed(msgs):
                        msg_chars = len(msg.get("content", ""))
                        if total_chars + msg_chars > cap and truncated:
                            break
                        truncated.insert(0, msg)
                        total_chars += msg_chars
                    context_msgs = truncated
                    # 补写 ledger_memory 以便下次直接命中
                    self._store_memory_context(entry_id, entry.session_id, context_msgs, entry.created)
            except Exception:
                pass
        return {
            "entry": entry,
            "context_messages": context_msgs,
        }

    def update_comment(self, entry_id: str, comment: str) -> bool:
        """更新用户备注。返回是否成功。"""
        conn = self._get_conn()
        comment = (comment or "")[:50]  # 截断到 50 字，防 None
        cur = conn.execute(
            "UPDATE ledger_entries SET comment=? WHERE id=? AND status='active'",
            (comment, entry_id)
        )
        conn.commit()
        return cur.rowcount > 0

    def _row_to_entry(self, row) -> LedgerEntry:
        """Row 对象转 LedgerEntry。"""
        return LedgerEntry(
            id=row["id"], content=row["content"], debt_type=row["debt_type"],
            source_context=row["source_context"] or "",
            wander_event_id=row["wander_event_id"],
            created=row["created"], status=row["status"],
            comment=row["comment"] if "comment" in row.keys() else "",
            session_id=row["session_id"] if "session_id" in row.keys() else "",
            last_active=row["last_active"] if "last_active" in row.keys() else "",
            settled_at=row["settled_at"] if "settled_at" in row.keys() else "",
        )

    def get_latest_entry(self) -> Optional[LedgerEntry]:
        """获取最近一条活跃条目。返回 None 表示账本为空。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM ledger_entries WHERE status='active' ORDER BY created DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return self._row_to_entry(row)

    def get_stats(self, debt_type: str) -> LedgerStats:
        """查询某类型的频次统计。"""
        conn = self._get_conn()
        now = datetime.now()
        seven_days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

        # 7天内次数
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM ledger_entries WHERE debt_type=? AND created >= ? AND status='active'",
            (debt_type, seven_days_ago)
        ).fetchone()
        count_7d = row["cnt"]

        # 总次数
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM ledger_entries WHERE debt_type=? AND status='active'",
            (debt_type,)
        ).fetchone()
        count_total = row["cnt"]

        # 排名：按各类型总次数排序
        all_types = conn.execute(
            "SELECT debt_type, COUNT(*) as cnt FROM ledger_entries WHERE status='active' GROUP BY debt_type ORDER BY cnt DESC"
        ).fetchall()
        total_types = len(all_types)
        rank = next((i + 1 for i, r in enumerate(all_types) if r["debt_type"] == debt_type), total_types)

        # 最近5条
        recent = conn.execute(
            "SELECT content, created FROM ledger_entries WHERE debt_type=? AND status='active' ORDER BY created DESC LIMIT 5",
            (debt_type,)
        ).fetchall()
        recent_entries = [f"[{r['created'][:10]}] {r['content'][:60]}" for r in recent]

        return LedgerStats(
            debt_type=debt_type, count_7d=count_7d, count_total=count_total,
            rank=rank, total_types=total_types, recent_entries=recent_entries
        )

    def list_entries(self, debt_type: str = None, limit: int = 20, offset: int = 0) -> List[LedgerEntry]:
        """列出条目（时间倒序，分页）。"""
        conn = self._get_conn()
        if debt_type:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE debt_type=? AND status='active' ORDER BY created DESC LIMIT ? OFFSET ?",
                (debt_type, limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE status='active' ORDER BY created DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count_entries(self, debt_type: str = None) -> int:
        """统计活跃条目总数。"""
        conn = self._get_conn()
        if debt_type:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM ledger_entries WHERE debt_type=? AND status='active'",
                (debt_type,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM ledger_entries WHERE status='active'"
            ).fetchone()
        return row["cnt"] if row else 0

    def get_all_stats(self) -> Dict[str, Any]:
        """获取所有类型的统计概览。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT debt_type, COUNT(*) as cnt FROM ledger_entries WHERE status='active' GROUP BY debt_type ORDER BY cnt DESC"
        ).fetchall()
        total = sum(r["cnt"] for r in rows)
        return {
            "total_entries": total,
            "type_breakdown": [{"debt_type": r["debt_type"], "count": r["cnt"]} for r in rows]
        }

    def get_distinct_types(self) -> List[str]:
        """获取所有活跃条目的不重复债务类型。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT debt_type FROM ledger_entries WHERE status='active' ORDER BY debt_type"
        ).fetchall()
        return [r["debt_type"] for r in rows]

    def update(self, entry_id: str, content: str = None, debt_type: str = None) -> Optional[LedgerEntry]:
        """更新条目内容或类型。返回更新后的条目，不存在则返回 None。"""
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT * FROM ledger_entries WHERE id=? AND status='active'", (entry_id,)
        ).fetchone()
        if not existing:
            return None
        new_content = content if content is not None else existing["content"]
        new_type = debt_type if debt_type is not None else existing["debt_type"]
        conn.execute(
            "UPDATE ledger_entries SET content=?, debt_type=? WHERE id=?",
            (new_content, new_type, entry_id)
        )
        conn.commit()
        return self._row_to_entry(conn.execute(
            "SELECT * FROM ledger_entries WHERE id=?", (entry_id,)
        ).fetchone())

    def delete(self, entry_id: str = "", content_keyword: str = "", delete_last: bool = False) -> tuple[bool, Optional[str]]:
        """删除条目（软删除）。支持 entry_id 精确匹配、content 模糊匹配、或删除最新一条。返回 (是否成功, 匹配到的内容)。"""
        conn = self._get_conn()
        if delete_last:
            latest = self.get_latest_entry()
            if latest:
                conn.execute(
                    "UPDATE ledger_entries SET status='dormant' WHERE id=? AND status='active'",
                    (latest.id,)
                )
                conn.commit()
                return True, latest.content
            return False, None
        if entry_id:
            cur = conn.execute(
                "UPDATE ledger_entries SET status='dormant' WHERE id=? AND status='active'",
                (entry_id,)
            )
            conn.commit()
            return cur.rowcount > 0, None
        if content_keyword:
            # 模糊匹配：找最新一条包含关键词的活跃条目
            row = conn.execute(
                "SELECT id, content FROM ledger_entries WHERE status='active' AND content LIKE ? ORDER BY created DESC LIMIT 1",
                (f"%{content_keyword}%",)
            ).fetchone()
            if row:
                conn.execute("UPDATE ledger_entries SET status='dormant' WHERE id=?", (row["id"],))
                conn.commit()
                return True, row["content"]
        return False, None

    def touch(self, entry_id: str) -> bool:
        """触碰条目：刷新 last_active，若为 dormant 则复活为 active。返回是否成功。"""
        conn = self._get_conn()
        now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "UPDATE ledger_entries SET last_active=?, "
            "status=CASE WHEN status='dormant' THEN 'active' ELSE status END "
            "WHERE id=? AND status IN ('active','dormant')",
            (now_ts, entry_id)
        )
        conn.commit()
        return cur.rowcount > 0

    def settle(self, entry_id: str) -> bool:
        """手动结算：直接归档(archived)，写 settled_at。不可逆——archived 不能被 touch 复活。"""
        conn = self._get_conn()
        now_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        cur = conn.execute(
            "UPDATE ledger_entries SET status='archived', settled_at=? WHERE id=? AND status IN ('active','dormant')",
            (now_ts, entry_id)
        )
        conn.commit()
        return cur.rowcount > 0

    def run_decay_cycle(self) -> dict:
        """执行一轮衰减：active→dormant→archived 自动过渡。返回变更计数。"""
        conn = self._get_conn()
        now = datetime.now()
        now_ts = now.strftime("%Y-%m-%dT%H:%M:%S")

        # 类型→重要性乘数（值越大越慢衰减）
        type_multiplier = {
            "wander_check": 0.5,    # 传感器异常，快速过期
            "serious_flirt": 1.0,   # 情趣记账，中等
            "promise_broken": 1.5,  # 失信，较慢
            "hidden_truth": 1.5,    # 隐瞒，较慢
        }
        DEFAULT_MULTIPLIER = 1.0

        active_to_dormant = 0
        dormant_to_archived = 0

        # 1. active → dormant：连续无人提超过阈值天数
        rows = conn.execute(
            "SELECT id, debt_type, last_active FROM ledger_entries WHERE status='active' AND last_active!=''"
        ).fetchall()
        for r in rows:
            try:
                last_active = datetime.fromisoformat(r["last_active"])
                days = max(0, (now - last_active).days)
                multiplier = type_multiplier.get(r["debt_type"], DEFAULT_MULTIPLIER)
                threshold = int(14 * multiplier)  # 基值 14 天 × 类型乘数
                if days >= threshold:
                    conn.execute("UPDATE ledger_entries SET status='dormant' WHERE id=?", (r["id"],))
                    active_to_dormant += 1
                    logger.info(f"Ledger 衰减: {r['id']} active→dormant ({days}d, threshold={threshold}d, type={r['debt_type']})")
            except (ValueError, TypeError):
                pass

        # 2. dormant → archived：超过更长阈值且未手动结算
        rows = conn.execute(
            "SELECT id, debt_type, last_active FROM ledger_entries WHERE status='dormant' AND settled_at=''"
        ).fetchall()
        for r in rows:
            try:
                last_active = datetime.fromisoformat(r["last_active"])
                days = max(0, (now - last_active).days)
                multiplier = type_multiplier.get(r["debt_type"], DEFAULT_MULTIPLIER)
                threshold = int(30 * multiplier)  # 基值 30 天 × 类型乘数
                if days >= threshold:
                    conn.execute(
                        "UPDATE ledger_entries SET status='archived', settled_at=? WHERE id=?",
                        (now_ts, r["id"])
                    )
                    dormant_to_archived += 1
                    logger.info(f"Ledger 衰减: {r['id']} dormant→archived ({days}d, threshold={threshold}d, type={r['debt_type']})")
            except (ValueError, TypeError):
                pass

        conn.commit()
        return {"active_to_dormant": active_to_dormant, "dormant_to_archived": dormant_to_archived}

    def get_old_active_entries(self, min_days: int = 7) -> list:
        """获取超过 N 天未 touch 的活跃条目（供上下文注入提醒 AI）。"""
        conn = self._get_conn()
        cutoff = (datetime.now() - timedelta(days=min_days)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = conn.execute(
            "SELECT content, debt_type, last_active FROM ledger_entries "
            "WHERE status='active' AND last_active!='' AND last_active<? "
            "ORDER BY last_active ASC LIMIT 10",
            (cutoff,)
        ).fetchall()
        return [{"content": r["content"], "debt_type": r["debt_type"], "last_active": r["last_active"]} for r in rows]


# 全局单例
_ledger_manager: Optional[LedgerManager] = None


def get_global_ledger_manager() -> LedgerManager:
    global _ledger_manager
    if _ledger_manager is None:
        _ledger_manager = LedgerManager()
    return _ledger_manager
