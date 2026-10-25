"""开放线索存储。

活动线索是 AI 主动选择继续保留在当前意识里的短句。SQLite 是唯一权威；
resolved 只改变状态，不复制到其他存储。
"""

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional


DEFAULT_DB_PATH = Path(__file__).parent.parent / "events" / "open_loops.db"
FOREGROUND_HOURS = max(1, int(os.environ.get("MIRROW_OPEN_LOOP_FOREGROUND_HOURS", "48")))
FADE_HOURS = max(FOREGROUND_HOURS + 1, int(os.environ.get("MIRROW_OPEN_LOOP_FADE_HOURS", "168")))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class OpenLoopStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS open_loops (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    wake_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(open_loops)").fetchall()}
            for name, definition in (
                ("last_reinforced_at", "TEXT NOT NULL DEFAULT ''"),
                ("fading_presented_at", "TEXT NOT NULL DEFAULT ''"),
                ("expired_at", "TEXT NOT NULL DEFAULT ''"),
                ("fact_confirmed_at", "TEXT NOT NULL DEFAULT ''"),
                ("fact_source_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("latest_progress", "TEXT NOT NULL DEFAULT ''"),
                ("fact_sources_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE open_loops ADD COLUMN {name} {definition}")
            conn.execute(
                "UPDATE open_loops SET last_reinforced_at=COALESCE(NULLIF(updated_at,''),created_at) "
                "WHERE last_reinforced_at=''"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS open_loop_transitions (
                    id TEXT PRIMARY KEY,
                    loop_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS open_loop_fade_offers (
                    turn_id TEXT PRIMARY KEY,
                    loop_id TEXT NOT NULL,
                    reinforcement_snapshot TEXT NOT NULL,
                    offered_at TEXT NOT NULL,
                    settled_at TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_open_loops_status ON open_loops(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_open_loop_fade_loop ON open_loop_fade_offers(loop_id,settled_at)"
            )

    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[dict]:
        return dict(row) if row is not None else None

    @staticmethod
    def _append_transition(
        conn: sqlite3.Connection,
        loop_id: str,
        action: str,
        from_status: str,
        to_status: str,
        reason: str = "",
        occurred_at: str = "",
    ) -> None:
        conn.execute(
            "INSERT INTO open_loop_transitions "
            "(id,loop_id,action,from_status,to_status,reason,occurred_at) VALUES (?,?,?,?,?,?,?)",
            (f"olt_{uuid.uuid4().hex[:16]}", loop_id, action, from_status, to_status, reason, occurred_at or _now_iso()),
        )

    def hold(self, content: str) -> dict:
        text = (content or "").strip()
        if not text:
            raise ValueError("开放线索内容不能为空")
        loop_id = f"loop_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO open_loops
                    (id, content, status, wake_at, created_at, updated_at, resolved_at,
                     last_reinforced_at, fading_presented_at, expired_at)
                VALUES (?, ?, 'active', '', ?, ?, '', ?, '', '')
                """,
                (loop_id, text, now, now, now),
            )
            self._append_transition(conn, loop_id, "hold", "", "active", occurred_at=now)
        return self.get(loop_id)

    def get(self, loop_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM open_loops WHERE id = ?", (loop_id,)
            ).fetchone()
        return self._row(row)

    def resolve_reference(self, reference: str) -> str:
        """Accept a durable ID or one exact existing sentence, never fuzzy match."""
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM open_loops WHERE id=?", (reference,)).fetchall()
            if not rows:
                rows = conn.execute("SELECT id FROM open_loops WHERE content=? AND status IN ('active','snoozed')",
                                    (reference,)).fetchall()
        if not rows:
            from .presentation import concern_line
            rows = [(item['id'],) for item in self.list_for_context()
                    if item['status'] in {'active', 'snoozed'}
                    and reference in {concern_line(item), concern_line(item).removeprefix('- ')}]
        if len(rows) != 1:
            raise ValueError("请用要修改的那条挂念的完整原句，重名时使用线索ID")
        return rows[0][0]

    def revise(self, loop_id: str, content: str) -> Optional[dict]:
        text = (content or "").strip()
        if not text:
            raise ValueError("修改后的开放线索不能为空")
        now = _now_iso()
        with self._connect() as conn:
            old = conn.execute("SELECT status FROM open_loops WHERE id=?", (loop_id,)).fetchone()
            cur = conn.execute(
                """
                UPDATE open_loops
                SET content = ?, status = 'active', wake_at = '',
                    updated_at = ?, resolved_at = '', expired_at = '',
                    last_reinforced_at = ?
                WHERE id = ? AND status NOT IN ('resolved','expired')
                """,
                (text, now, now, loop_id),
            )
            if cur.rowcount:
                self._append_transition(conn, loop_id, "revise", str(old[0] if old else ""), "active", occurred_at=now)
        return self.get(loop_id) if cur.rowcount else None

    def snooze(self, loop_id: str, wake_at: str) -> Optional[dict]:
        wake = (wake_at or "").strip()
        if not wake:
            raise ValueError("暂放开放线索需要 wake_at")
        try:
            datetime.fromisoformat(wake)
        except ValueError as exc:
            raise ValueError("wake_at 必须是 ISO 日期时间") from exc
        now = _now_iso()
        with self._connect() as conn:
            old = conn.execute("SELECT status FROM open_loops WHERE id=?", (loop_id,)).fetchone()
            cur = conn.execute(
                """
                UPDATE open_loops
                SET status = 'snoozed', wake_at = ?, updated_at = ?,
                    last_reinforced_at = ?, expired_at = ''
                WHERE id = ? AND status NOT IN ('resolved','expired')
                """,
                (wake, now, now, loop_id),
            )
            if cur.rowcount:
                self._append_transition(conn, loop_id, "snooze", str(old[0] if old else ""), "snoozed", wake, now)
        return self.get(loop_id) if cur.rowcount else None

    def release(self, loop_id: str) -> Optional[dict]:
        now = _now_iso()
        with self._connect() as conn:
            old = conn.execute("SELECT status FROM open_loops WHERE id=?", (loop_id,)).fetchone()
            cur = conn.execute(
                """
                UPDATE open_loops
                SET status = 'resolved', wake_at = '', updated_at = ?, resolved_at = ?
                WHERE id = ? AND status NOT IN ('resolved','expired')
                """,
                (now, now, loop_id),
            )
            if cur.rowcount:
                self._append_transition(conn, loop_id, "release", str(old[0] if old else ""), "resolved", occurred_at=now)
        return self.get(loop_id) if cur.rowcount else None

    def reinforce(self, loop_id: str) -> Optional[dict]:
        """Keep an existing concern alive without rewriting its content."""
        now = _now_iso()
        with self._connect() as conn:
            old = conn.execute("SELECT status FROM open_loops WHERE id=?", (loop_id,)).fetchone()
            cur = conn.execute(
                """
                UPDATE open_loops
                SET status='active', wake_at='', updated_at=?, last_reinforced_at=?, expired_at=''
                WHERE id=? AND status NOT IN ('resolved','expired')
                """,
                (now, now, loop_id),
            )
            if cur.rowcount:
                self._append_transition(conn, loop_id, "reinforce", str(old[0] if old else ""), "active", occurred_at=now)
        return self.get(loop_id) if cur.rowcount else None

    def _wake_due(self) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE open_loops
                SET status = 'active', wake_at = '', updated_at = ?
                WHERE status = 'snoozed' AND wake_at != '' AND wake_at <= ?
                """,
                (now, now),
            )

    def list_active(self) -> list[dict]:
        self._wake_due()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM open_loops
                WHERE status = 'active'
                ORDER BY created_at ASC
                """
            ).fetchall()
        from .progress import inspect_progress
        return inspect_progress([dict(row) for row in rows], self.db_path.parent / 'event_chronicle.db')

    @staticmethod
    def _age_hours(item: dict, now: datetime) -> float:
        raw = str(item.get("last_reinforced_at") or item.get("updated_at") or item.get("created_at") or "")
        try:
            origin = datetime.fromisoformat(raw)
            if origin.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=origin.tzinfo)
            elif origin.tzinfo is None and now.tzinfo is not None:
                origin = origin.replace(tzinfo=now.tzinfo)
            return max(0.0, (now - origin).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            return float("inf")

    def list_for_context(self, turn_id: str = "") -> list[dict]:
        """只读读取 open loop，供 Context Builder 使用。

        ``list_active()`` 历史上会在读取时唤醒 snoozed 记录；上下文
        构建是 read path，不能因为一次 prompt 读取就修改权威 open loop
        状态，所以新 affect 选择器使用这个无副作用入口。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM open_loops ORDER BY created_at ASC"
            ).fetchall()
            offered_loop = ""
            if turn_id:
                offer = conn.execute(
                    "SELECT loop_id FROM open_loop_fade_offers WHERE turn_id=? AND settled_at=''",
                    (turn_id,),
                ).fetchone()
                offered_loop = str(offer[0]) if offer else ""
        current = datetime.now()
        result = []
        for row in rows:
            item = dict(row)
            age = self._age_hours(item, current)
            if item.get("status") == "active":
                if item.get("id") == offered_loop:
                    item["lifecycle_state"] = "fading"
                elif age <= FOREGROUND_HOURS:
                    item["lifecycle_state"] = "foreground"
                else:
                    item["lifecycle_state"] = "background"
            elif item.get("status") == "snoozed":
                item["lifecycle_state"] = "snoozed"
            else:
                item["lifecycle_state"] = str(item.get("status") or "resolved")
            item["age_hours"] = round(age, 2) if age != float("inf") else None
            result.append(item)
        from .progress import inspect_progress
        return inspect_progress(result, self.db_path.parent / 'event_chronicle.db')

    def list_lifecycle(self, limit: int = 50) -> list[dict]:
        """Read-only, bounded lifecycle view for Human; never wakes or fades a loop."""
        items = self.list_for_context()
        active = [item for item in items if item.get("status") in {"active", "snoozed"}]
        inactive = [item for item in items if item.get("status") not in {"active", "snoozed"}]
        active.sort(key=lambda item: str(item.get("last_reinforced_at") or item.get("updated_at") or ""), reverse=True)
        inactive.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        selected = (active + inactive)[:max(1, min(100, int(limit)))]
        with self._connect() as conn:
            for item in selected:
                loop_id = str(item.get("id") or "")
                transitions = conn.execute(
                    """SELECT action,from_status,to_status,occurred_at FROM open_loop_transitions
                       WHERE loop_id=? ORDER BY occurred_at DESC LIMIT 8""", (loop_id,),
                ).fetchall()
                offer = conn.execute(
                    """SELECT offered_at,settled_at,outcome FROM open_loop_fade_offers
                       WHERE loop_id=? ORDER BY offered_at DESC LIMIT 1""", (loop_id,),
                ).fetchone()
                item["transitions"] = [dict(row) for row in reversed(transitions)]
                item["last_fade_offer"] = dict(offer) if offer else None
                if item.get("status") == "active":
                    if offer and not offer["settled_at"]:
                        item["lifecycle_state"] = "fading"
                    elif (item.get("age_hours") or 0) >= FADE_HOURS and (
                        not offer or offer["outcome"] == "not_projected"
                    ):
                        item["lifecycle_state"] = "awaiting_fade"
        return selected

    def claim_fading_for_turn(self, turn_id: str, now: str = "") -> Optional[dict]:
        """Claim at most one old concern for its single final resurfacing."""
        turn = str(turn_id or "").strip()
        if not turn:
            return None
        current = datetime.fromisoformat(now) if now else datetime.now()
        now_value = current.isoformat(timespec="seconds")
        cutoff = (current - timedelta(hours=FADE_HOURS)).isoformat(timespec="seconds")
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM open_loop_fade_offers WHERE turn_id=?", (turn,)
            ).fetchone()
            if existing:
                return dict(existing)
            row = conn.execute(
                """
                SELECT loop.* FROM open_loops AS loop
                WHERE loop.status='active'
                  AND COALESCE(NULLIF(loop.last_reinforced_at,''),loop.updated_at,loop.created_at) <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM open_loop_fade_offers AS offer
                      WHERE offer.loop_id=loop.id
                        AND offer.outcome!='not_projected'
                  )
                ORDER BY COALESCE(NULLIF(loop.last_reinforced_at,''),loop.updated_at,loop.created_at) ASC
                LIMIT 1
                """,
                (cutoff,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            snapshot = str(item.get("last_reinforced_at") or item.get("updated_at") or item.get("created_at") or "")
            conn.execute(
                "INSERT INTO open_loop_fade_offers "
                "(turn_id,loop_id,reinforcement_snapshot,offered_at,settled_at,outcome) VALUES (?,?,?,?, '', '')",
                (turn, item["id"], snapshot, now_value),
            )
            conn.execute(
                "UPDATE open_loops SET fading_presented_at=? WHERE id=?",
                (now_value, item["id"]),
            )
            offer = conn.execute("SELECT * FROM open_loop_fade_offers WHERE turn_id=?", (turn,)).fetchone()
            return dict(offer) if offer else None

    def cancel_fading_offer(self, turn_id: str, now: str = "") -> Optional[dict]:
        """Keep a concern when its claimed projection never reached a prompt."""
        turn = str(turn_id or "").strip()
        if not turn:
            return None
        now_value = str(now or _now_iso())
        with self._connect() as conn:
            offer = conn.execute(
                "SELECT * FROM open_loop_fade_offers WHERE turn_id=? AND settled_at=''", (turn,)
            ).fetchone()
            if not offer:
                return None
            conn.execute(
                "UPDATE open_loop_fade_offers SET settled_at=?,outcome='not_projected' WHERE turn_id=?",
                (now_value, turn),
            )
            conn.execute(
                "UPDATE open_loops SET fading_presented_at='' WHERE id=? AND fading_presented_at=?",
                (offer["loop_id"], offer["offered_at"]),
            )
            return {**dict(offer), "settled_at": now_value, "outcome": "not_projected"}

    def settle_fading_offer(self, turn_id: str, now: str = "") -> Optional[dict]:
        """Expire an offered concern unless AI explicitly touched it this turn."""
        turn = str(turn_id or "").strip()
        if not turn:
            return None
        now_value = str(now or _now_iso())
        with self._connect() as conn:
            offer = conn.execute(
                "SELECT * FROM open_loop_fade_offers WHERE turn_id=? AND settled_at=''", (turn,)
            ).fetchone()
            if not offer:
                return None
            loop = conn.execute("SELECT * FROM open_loops WHERE id=?", (offer["loop_id"],)).fetchone()
            outcome = "missing"
            if loop:
                item = dict(loop)
                touched = (
                    str(item.get("last_reinforced_at") or "") != str(offer["reinforcement_snapshot"] or "")
                    or str(item.get("updated_at") or "") > str(offer["offered_at"] or "")
                )
                if item.get("status") != "active":
                    outcome = str(item.get("status") or "changed")
                elif touched:
                    outcome = "reinforced"
                else:
                    conn.execute(
                        "UPDATE open_loops SET status='expired', expired_at=?, updated_at=? WHERE id=?",
                        (now_value, now_value, item["id"]),
                    )
                    self._append_transition(
                        conn, item["id"], "expire", "active", "expired",
                        "fading_offer_not_reinforced", now_value,
                    )
                    outcome = "expired"
            conn.execute(
                "UPDATE open_loop_fade_offers SET settled_at=?, outcome=? WHERE turn_id=?",
                (now_value, outcome, turn),
            )
            return {**dict(offer), "settled_at": now_value, "outcome": outcome}

    def list_resolved(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM open_loops
                WHERE status IN ('resolved','expired')
                ORDER BY COALESCE(NULLIF(resolved_at,''),NULLIF(expired_at,''),updated_at) DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]


_store: Optional[OpenLoopStore] = None


def get_open_loop_store() -> OpenLoopStore:
    global _store
    if _store is None:
        _store = OpenLoopStore()
    return _store
