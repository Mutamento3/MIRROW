"""Append-only local AI-post actions beside the Wander source evidence.

The projection contains only bounded display fields and can be rebuilt from
Wander nodes plus actions. It is not another copy of the post body.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .links import parse_share_link

DEFAULT_DB = Path(__file__).resolve().parents[1] / "events" / "wander_runtime.db"


class XhsPostStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _schema(conn: sqlite3.Connection) -> None:
        conn.execute("""CREATE TABLE IF NOT EXISTS xhs_post_refs (
            post_key TEXT PRIMARY KEY, node_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL, summary TEXT NOT NULL,
            source_url TEXT NOT NULL, link_status TEXT NOT NULL,
            canonical_post_key TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL, saved INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )""")
        if "canonical_post_key" not in {row[1] for row in conn.execute("PRAGMA table_info(xhs_post_refs)")}:
            conn.execute("ALTER TABLE xhs_post_refs ADD COLUMN canonical_post_key TEXT NOT NULL DEFAULT ''")
        conn.execute("""CREATE TABLE IF NOT EXISTS xhs_post_actions (
            action_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            post_key TEXT NOT NULL REFERENCES xhs_post_refs(post_key),
            node_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('save','unsave')),
            reason TEXT NOT NULL, actor TEXT NOT NULL,
            changed INTEGER NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS xhs_post_link_evidence (
            evidence_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
            post_key TEXT NOT NULL, share_url TEXT NOT NULL,
            source TEXT NOT NULL, confirmed_same_post INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS xhs_post_comment_reports (
            report_id TEXT PRIMARY KEY, message_id TEXT NOT NULL UNIQUE,
            node_id TEXT NOT NULL, actor TEXT NOT NULL,
            report_type TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_xhs_saved ON xhs_post_refs(saved,updated_at)")

    @staticmethod
    def _node(conn: sqlite3.Connection, node_id: str) -> tuple[sqlite3.Row, dict]:
        row = conn.execute("""SELECT n.node_id,n.source_payload,n.source_summary,n.completed_at,n.reflection,
            n.execution_status,a.activity_type FROM wander_nodes n
            JOIN wander_activities a ON a.activity_id=n.activity_id WHERE n.node_id=?""", (node_id,)).fetchone()
        if row is None or row["activity_type"] != "browse_xiaohongshu" or row["execution_status"] != "succeeded":
            raise ValueError("没有找到已完成的小红书浏览节点")
        try:
            payload = json.loads(row["source_payload"] or "{}")
        except (TypeError, ValueError):
            raise ValueError("帖子来源记录格式不正确") from None
        if payload.get("status") != "success" or not payload.get("source_id"):
            raise ValueError("节点没有成功读取帖子")
        return row, payload

    def save_from_node(self, node_id: str, *, reason: str = "", actor: str = "k_wander",
                       idempotency_key: str = "", save: bool = True) -> dict:
        if not node_id or len(node_id) > 120 or not idempotency_key or len(idempotency_key) > 180:
            raise ValueError("缺少有效的节点或幂等标识")
        if actor not in {"k_wander", "k_tool", "human"}:
            raise ValueError("留帖操作者不正确")
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            self._schema(conn)
            existing = conn.execute("SELECT * FROM xhs_post_actions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return dict(existing)
            node, payload = self._node(conn, node_id)
            key = "xhs-node:" + node_id
            link = ""
            link_status = "unavailable"
            canonical_key = ""
            try:
                parsed = parse_share_link(str(payload.get("post_url") or ""))
                # A URL obtained during the same successful source read may be
                # displayed, but still does not prove a later reopen succeeded.
                link, link_status = parsed["url"], parsed["link_status"]
                canonical_key = parsed["post_key"]
            except ValueError:
                pass
            title = " ".join(str(payload.get("title") or "").split())[:160]
            summary = " ".join(str(payload.get("content_summary") or node["source_summary"] or "").split())[:400]
            conn.execute("""INSERT INTO xhs_post_refs
                (post_key,node_id,title,summary,source_url,link_status,canonical_post_key,first_seen_at,saved,updated_at)
                VALUES (?,?,?,?,?,?,?,?,0,?)
                ON CONFLICT(post_key) DO UPDATE SET
                    title=excluded.title,summary=excluded.summary,
                    source_url=CASE WHEN excluded.source_url!='' THEN excluded.source_url ELSE xhs_post_refs.source_url END,
                    link_status=CASE WHEN excluded.source_url!='' THEN excluded.link_status ELSE xhs_post_refs.link_status END,
                    canonical_post_key=CASE WHEN excluded.canonical_post_key!='' THEN excluded.canonical_post_key ELSE xhs_post_refs.canonical_post_key END,
                    updated_at=excluded.updated_at""",
                (key, node_id, title, summary, link, link_status, canonical_key, node["completed_at"] or now, now))
            identity = conn.execute("SELECT canonical_post_key FROM xhs_post_refs WHERE post_key=?", (key,)).fetchone()["canonical_post_key"]
            if identity:
                current = conn.execute("SELECT MAX(saved) FROM xhs_post_refs WHERE canonical_post_key=?", (identity,)).fetchone()[0]
            else:
                current = conn.execute("SELECT saved FROM xhs_post_refs WHERE post_key=?", (key,)).fetchone()["saved"]
            changed = int(bool(current) != save)
            action = "save" if save else "unsave"
            action_id = uuid4().hex
            conn.execute("""INSERT INTO xhs_post_actions
                (action_id,idempotency_key,post_key,node_id,action,reason,actor,changed,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (action_id, idempotency_key, key, node_id, action, " ".join(reason.split())[:300], actor, changed, now))
            if identity:
                conn.execute("UPDATE xhs_post_refs SET saved=?,updated_at=? WHERE canonical_post_key=?", (int(save), now, identity))
            elif changed:
                conn.execute("UPDATE xhs_post_refs SET saved=?,updated_at=? WHERE post_key=?", (int(save), now, key))
            return dict(conn.execute("SELECT * FROM xhs_post_actions WHERE action_id=?", (action_id,)).fetchone())

    def bind_confirmed_link(self, node_id: str, share_text: str, *, confirmed_same_post: bool) -> dict:
        """Attach a human-checked share URL to this exact read node, not a title match."""
        if not confirmed_same_post:
            raise ValueError("请先打开链接，确认它与 AI 读到的是同一篇帖子")
        parsed = parse_share_link(share_text)
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            self._schema(conn)
            node, payload = self._node(conn, node_id)
            key = "xhs-node:" + node_id
            title = " ".join(str(payload.get("title") or "").split())[:160]
            summary = " ".join(str(payload.get("content_summary") or node["source_summary"] or "").split())[:400]
            conn.execute("""INSERT INTO xhs_post_refs
                (post_key,node_id,title,summary,source_url,link_status,canonical_post_key,first_seen_at,saved,updated_at)
                VALUES (?,?,?,?,?,'user_confirmed',?,?,0,?)
                ON CONFLICT(post_key) DO UPDATE SET source_url=excluded.source_url,
                    link_status='user_confirmed',canonical_post_key=excluded.canonical_post_key,
                    updated_at=excluded.updated_at""",
                (key, node_id, title, summary, parsed["url"], parsed["post_key"], node["completed_at"] or now, now))
            evidence_id = uuid4().hex
            conn.execute("""INSERT INTO xhs_post_link_evidence
                (evidence_id,node_id,post_key,share_url,source,confirmed_same_post,created_at)
                VALUES (?,?,?,?,'human_manual',1,?)""",
                (evidence_id, node_id, key, parsed["url"], now))
            identity_saved = conn.execute("SELECT MAX(saved) FROM xhs_post_refs WHERE canonical_post_key=?", (parsed["post_key"],)).fetchone()[0]
            conn.execute("UPDATE xhs_post_refs SET saved=? WHERE canonical_post_key=?", (int(identity_saved or 0), parsed["post_key"]))
            return {"post_key": key, "canonical_post_key": parsed["post_key"], "source_url": parsed["url"],
                    "link_status": "user_confirmed", "evidence_id": evidence_id}

    def link_for_node(self, node_id: str) -> dict:
        if not self.db_path.is_file():
            return {}
        with closing(self._connect()) as conn:
            self._schema(conn)
            row = conn.execute("SELECT source_url,link_status FROM xhs_post_refs WHERE node_id=?", (node_id,)).fetchone()
            return dict(row) if row else {}

    def comment_report(self, message_id: str) -> dict:
        if not self.db_path.is_file():
            return {}
        with closing(self._connect()) as conn:
            self._schema(conn)
            row = conn.execute("SELECT * FROM xhs_post_comment_reports WHERE message_id=?", (message_id,)).fetchone()
            return dict(row) if row else {}

    def record_comment_self_report(self, message_id: str, node_id: str) -> dict:
        """Record 人类伙伴's own statement, never an XHS platform receipt."""
        if not message_id or len(message_id) > 180:
            raise ValueError("缺少评论邀请消息")
        now = datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            self._schema(conn)
            self._node(conn, node_id)
            conn.execute("""INSERT OR IGNORE INTO xhs_post_comment_reports
                (report_id,message_id,node_id,actor,report_type,created_at)
                VALUES (?,?,?,'human','self_reported_comment',?)""",
                (uuid4().hex, message_id, node_id, now))
            return dict(conn.execute("SELECT * FROM xhs_post_comment_reports WHERE message_id=?", (message_id,)).fetchone())

    def list_posts(self, *, saved_only: bool = True, query: str = "", limit: int = 100) -> list[dict]:
        if not self.db_path.is_file():
            return []
        with closing(self._connect()) as conn, conn:
            self._schema(conn)
            sql = "SELECT r.*,a.reason AS last_reason,(SELECT COUNT(*) FROM xhs_post_comment_reports c WHERE c.node_id=r.node_id) AS comment_self_reports FROM xhs_post_refs r LEFT JOIN xhs_post_actions a ON a.action_id=(SELECT a2.action_id FROM xhs_post_actions a2 WHERE a2.post_key=r.post_key AND a2.action='save' ORDER BY a2.created_at DESC,a2.rowid DESC LIMIT 1) WHERE 1=1"
            params: list[object] = []
            if saved_only:
                sql += " AND r.saved=1"
            if query.strip():
                sql += " AND (r.title LIKE ? OR r.summary LIKE ? OR a.reason LIKE ?)"
                pattern = "%" + query.strip()[:100] + "%"
                params.extend([pattern] * 3)
            sql += " ORDER BY r.updated_at DESC LIMIT ?"
            params.append(max(1, min(limit * 3, 600)))
            rows = [dict(row) for row in conn.execute(sql, params)]
            seen: set[str] = set()
            result = []
            for row in rows:
                identity = row["canonical_post_key"] or row["post_key"]
                if identity in seen:
                    continue
                seen.add(identity)
                result.append(row)
                if len(result) >= max(1, min(limit, 200)):
                    break
            return result
