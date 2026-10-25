"""
MIRROW 导入导出模块
- 导出：从 event_chronicle.db 生成精简 SQLite 文件（含 conversation_messages/bookmarks/conversation_days）
- 导入：从 SQLite 文件读取消息+书签，写入 session JSON + SQLite
"""
import sqlite3
import os
import shutil
import uuid
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from time_utils import parse_ts

logger = logging.getLogger(__name__)

# 导入文件临时存放目录
IMPORTS_DIR = os.path.join(os.path.dirname(__file__), "data", "imports")
os.makedirs(IMPORTS_DIR, exist_ok=True)

# 活跃导入会话：import_id -> {"path": str, "created_at": str}
_active_imports: Dict[str, Dict[str, str]] = {}

# 导出保留的表
_EXPORT_TABLES = {"conversation_messages", "bookmarks", "conversation_days"}


def _build_tool_summary(tool_calls: List[Dict]) -> Optional[str]:
    """从 tool_calls 列表构建 tool_summary 文本。"""
    if not tool_calls:
        return None
    parts = []
    for tc in tool_calls:
        name = tc.get("tool", "") or tc.get("name", "")
        desc = (tc.get("description", "") or "")[:60]
        if name:
            parts.append(f"{name}({desc})" if desc else name)
    return ", ".join(parts) if parts else None


# ─── 导出 ────────────────────────────────────────────

async def export_to_sqlite() -> str:
    """生成导出 SQLite 文件，返回文件路径。"""
    from event_chronicle import get_global_chronicle

    chronicle = get_global_chronicle()
    source_db = chronicle.db_path

    export_id = uuid.uuid4().hex[:12]
    export_path = os.path.join(IMPORTS_DIR, f"export_{export_id}.db")

    # 复制源数据库
    shutil.copy2(source_db, export_path)

    # 删除不需要的运行时表
    with sqlite3.connect(export_path) as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        all_tables = {r[0] for r in cur.fetchall()}
        tables_to_drop = all_tables - _EXPORT_TABLES
        for t in tables_to_drop:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass

        # 添加 export_meta 表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS export_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO export_meta VALUES (?, ?)",
            ("exporter_version", "1.0")
        )
        conn.execute(
            "INSERT OR REPLACE INTO export_meta VALUES (?, ?)",
            ("exported_at", datetime.now().isoformat())
        )
        conn.execute(
            "INSERT OR REPLACE INTO export_meta VALUES (?, ?)",
            ("source", "MIRROW")
        )
        # 重建 conversation_days（从活跃表 conversation_messages 统计，
        # 替代源库中已停更的旧索引——生产消息写入不再更新该表）
        conn.execute("DELETE FROM conversation_days")
        conn.execute(
            "INSERT OR REPLACE INTO conversation_days (date, message_count, first_message_at, last_message_at) "
            "SELECT active_date, COUNT(*), MIN(timestamp), MAX(timestamp) "
            "FROM conversation_messages "
            "WHERE active_date IS NOT NULL AND active_date != '' "
            "GROUP BY active_date"
        )
        conn.commit()
        conn.execute("VACUUM")

    logger.info(f"Export created: {export_path}")
    return export_path


# ─── 导入 ────────────────────────────────────────────

def validate_import_file(file_path: str) -> Optional[Dict]:
    """验证 SQLite 文件是否包含必要的表。返回元数据或 None。"""
    try:
        with sqlite3.connect(file_path) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {r[0] for r in tables}

            if "conversation_messages" not in table_names:
                logger.warning(f"Import validation failed: missing conversation_messages table")
                return None

            # 读取 export_meta
            meta = {}
            if "export_meta" in table_names:
                try:
                    rows = conn.execute("SELECT key, value FROM export_meta").fetchall()
                    meta = {r[0]: r[1] for r in rows}
                except Exception:
                    pass

            # 检查消息表中是否有数据
            count = conn.execute(
                "SELECT COUNT(*) FROM conversation_messages"
            ).fetchone()[0]

            if count == 0:
                logger.warning("Import validation: conversation_messages is empty")
                return None

            return {
                "message_count": count,
                "has_bookmarks": "bookmarks" in table_names,
                "exporter_version": meta.get("exporter_version", "unknown"),
                "exported_at": meta.get("exported_at", ""),
            }
    except sqlite3.Error as e:
        logger.error(f"Import validation sqlite error: {e}")
        return None


def get_import_dates(import_id: str) -> List[Dict]:
    """列出导入文件中可用的日期及消息数量，含目标系统是否已有数据标记。"""
    if import_id not in _active_imports:
        return []

    file_path = _active_imports[import_id]["path"]

    # 查询目标系统中已有消息的日期（从活跃表 conversation_messages 实时聚合——
    # conversation_days 已停更废弃，生产消息写入不再更新它，查它会把近期日期误判为未导入）
    existing_dates = set()
    try:
        from event_chronicle import get_global_chronicle
        chronicle = get_global_chronicle()
        with sqlite3.connect(chronicle.db_path) as target_conn:
            rows = target_conn.execute(
                "SELECT DISTINCT active_date FROM conversation_messages "
                "WHERE active_date IS NOT NULL AND active_date != ''"
            ).fetchall()
            existing_dates = {r[0] for r in rows}
    except Exception:
        pass

    with sqlite3.connect(file_path) as conn:
        conn.row_factory = sqlite3.Row
        # 兼容新旧 schema：优先 active_date，回退 date
        try:
            rows = conn.execute(
                "SELECT active_date, calendar_date, COUNT(*) as cnt FROM conversation_messages "
                "GROUP BY active_date ORDER BY active_date DESC"
            ).fetchall()
            return [
                {
                    "active_date": r["active_date"],
                    "calendar_date": r["calendar_date"] or r["active_date"],
                    "message_count": r["cnt"],
                    "already_exists": r["active_date"] in existing_dates,
                }
                for r in rows
            ]
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT date as active_date, COUNT(*) as cnt FROM conversation_messages "
                "GROUP BY date ORDER BY date DESC"
            ).fetchall()
            return [
                {
                    "active_date": r["active_date"],
                    "calendar_date": r["active_date"],
                    "message_count": r["cnt"],
                    "already_exists": r["active_date"] in existing_dates,
                }
                for r in rows
            ]


async def import_dates(import_id: str, dates: List[str],
                       target_session_id: Optional[str] = None) -> Dict:
    """导入指定日期的消息。返回导入结果摘要。"""
    if import_id not in _active_imports:
        raise ValueError("导入会话已过期")

    file_path = _active_imports[import_id]["path"]
    from event_chronicle import get_global_chronicle
    from session_manager import get_global_session_manager

    chronicle = get_global_chronicle()
    smgr = get_global_session_manager()

    # 读取所有指定日期的消息
    all_messages = []
    with sqlite3.connect(file_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in dates)
        # 兼容新旧 schema：优先 active_date，回退 date
        try:
            rows = conn.execute(
                f"SELECT * FROM conversation_messages WHERE active_date IN ({placeholders}) "
                "ORDER BY active_date, timestamp",
                dates
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                f"SELECT * FROM conversation_messages WHERE date IN ({placeholders}) "
                "ORDER BY date, timestamp",
                dates
            ).fetchall()
        all_messages = [dict(r) for r in rows]

    if not all_messages:
        return {"imported_messages": 0, "imported_bookmarks": 0, "session_id": target_session_id}

    # 解决或创建目标会话
    if target_session_id:
        session = await smgr.get_session(target_session_id)
        if not session:
            target_session_id = None

    if not target_session_id:
        date_label = dates[0] if len(dates) == 1 else f"{dates[0]}~{dates[-1]}"
        session_title = f"{date_label}"
        session = await smgr.create_session(session_title)
        target_session_id = session["id"]

    # 构建 messages 列表用于 session JSON
    new_messages = []
    for m in all_messages:
        msg_id = m.get("message_id") or m.get("id") or f"msg_{m.get('timestamp', '')}"
        # 确保类型前缀保留
        msg_id_str = str(msg_id)

        # 构建 thinking_chain
        thinking_chain = None
        rc = m.get("reasoning_content")
        if rc:
            thinking_chain = [{"content": rc, "timestamp": m.get("timestamp", "")}]

        # 构建 tool_calls（简化版，从 tool_summary 重建）
        tool_calls = None
        ts = m.get("tool_summary")
        if ts:
            # 从 "tool_name(desc), tool_name2(desc2)" 重建
            tool_calls = []
            for part in ts.split(", "):
                if "(" in part:
                    t_name = part[:part.index("(")]
                    t_desc = part[part.index("(")+1:].rstrip(")")
                    tool_calls.append({
                        "tool": t_name,
                        "description": t_desc,
                        "result": "",
                    })
                else:
                    tool_calls.append({
                        "tool": part,
                        "description": "",
                        "result": "",
                    })

        # 确定 isWander / isSentinel
        is_wander = bool(m.get("is_wander"))
        is_sentinel = bool(m.get("is_sentinel"))

        new_msg = {
            "id": msg_id_str,
            "role": m["role"],
            "content": m["content"],
            "timestamp": m["timestamp"],
            "isWander": is_wander or None,
            "isSentinel": is_sentinel or None,
        }
        if thinking_chain:
            new_msg["thinkingChain"] = thinking_chain
        if tool_calls:
            new_msg["toolCalls"] = tool_calls
        if rc:
            new_msg["reasoning_content"] = rc

        new_messages.append(new_msg)

    # 分批写入 session JSON（防大量消息一次写入内存溢出）
    BATCH_SIZE = 50
    total = len(new_messages)
    for i in range(0, total, BATCH_SIZE):
        batch = new_messages[i:i + BATCH_SIZE]
        existing = await smgr.get_session(target_session_id)
        if existing:
            existing_msgs = existing.get("messages", [])
            existing_msgs.extend(batch)
            existing_msgs.sort(key=lambda x: parse_ts(x.get("timestamp", "")))
            existing["messages"] = existing_msgs
            await smgr.update_session(target_session_id, existing)
        await smgr.flush_session(target_session_id)

    # 写入目标 SQLite（conversation_messages + conversation_days）
    for m in all_messages:
        try:
            chronicle.add_conversation_message(
                active_date=m.get("active_date", m.get("date", "")),
                calendar_date=m.get("calendar_date", m.get("active_date", m.get("date", ""))),
                session_id=target_session_id,
                timestamp=m["timestamp"],
                role=m["role"],
                content=m["content"],
                reasoning_content=m.get("reasoning_content"),
                message_id=str(m.get("message_id") or m.get("id", "")),
                is_wander=m.get("is_wander", 0) or 0,
                is_sentinel=m.get("is_sentinel", 0) or 0,
                is_reminder=m.get("is_reminder", 0) or 0,
                tool_summary=m.get("tool_summary"),
                tool_calls=m.get("tool_calls"),
                event_type=m.get("event_type", ""),
                images=m.get("images"),
                voice_attachment=m.get("voice_attachment"),
                thinking_chain_json=m.get("thinking_chain_json"),
            )
        except Exception as e:
            logger.warning(f"Failed to write imported message to SQLite: {e}")

    # 更新 conversation_days 计数（N4: 重新统计该日期的消息数）
    for date_str in dates:
        try:
            with sqlite3.connect(chronicle.db_path) as tc:
                row = tc.execute(
                    "SELECT COUNT(*) FROM conversation_messages WHERE active_date=?",
                    (date_str,)
                ).fetchone()
                msg_count = row[0] if row else 0
                if msg_count > 0:
                    # 从导入的消息中获取真实的首尾时间戳
                    ts_row = tc.execute(
                        "SELECT MIN(timestamp), MAX(timestamp) FROM conversation_messages WHERE active_date=?",
                        (date_str,)
                    ).fetchone()
                    chronicle.upsert_conversation_day(
                        date=date_str,
                        count=msg_count,
                        first_at=ts_row[0] if ts_row and ts_row[0] else "",
                        last_at=ts_row[1] if ts_row and ts_row[1] else "",
                    )
        except Exception:
            pass

    # 导入书签
    imported_bookmarks = 0
    try:
        with sqlite3.connect(file_path) as conn:
            conn.row_factory = sqlite3.Row
            source_msg_ids = {str(m.get("message_id") or m.get("id", "")) for m in all_messages}
            if source_msg_ids and "bookmarks" in {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }:
                placeholders = ",".join("?" for _ in source_msg_ids)
                bm_rows = conn.execute(
                    f"SELECT * FROM bookmarks WHERE original_msg_id IN ({placeholders})",
                    list(source_msg_ids)
                ).fetchall()
                for bm in bm_rows:
                    bm = dict(bm)
                    bm_content = bm.get("content", "")
                    bm_collected_by = bm.get("collected_by", "user")

                    # A bookmark is a source-backed SQLite record, not a memory bucket.
                    bucket_id = bm.get("bucket_id") or str(uuid.uuid4())

                    # 写入元数据
                    try:
                        chronicle.add_bookmark(
                            bucket_id=bucket_id,
                            original_msg_id=bm["original_msg_id"],
                            session_id=target_session_id,
                            original_timestamp=bm.get("original_timestamp", ""),
                            collected_by=bm_collected_by,
                            content=bm_content[:500],
                        )
                        imported_bookmarks += 1
                    except Exception as e:
                        logger.warning(f"Failed to import bookmark: {e}")
    except Exception as e:
        logger.warning(f"Bookmark import failed: {e}")

    return {
        "imported_messages": total,
        "imported_bookmarks": imported_bookmarks,
        "session_id": target_session_id,
        "title": session.get("title", "") if session else "",
    }


def cleanup_import(import_id: str):
    """清理导入文件。"""
    if import_id in _active_imports:
        file_path = _active_imports[import_id]["path"]
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        del _active_imports[import_id]


def cleanup_stale_imports(max_age_hours: int = 24):
    """清理超过 max_age_hours 的导入/导出文件。"""
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    if os.path.isdir(IMPORTS_DIR):
        for fname in os.listdir(IMPORTS_DIR):
            fpath = os.path.join(IMPORTS_DIR, fname)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    logger.info(f"Cleaned up stale import/export file: {fname}")
            except Exception:
                pass
    # 清理过期活跃导入
    stale_ids = [
        iid for iid, info in _active_imports.items()
        if datetime.fromisoformat(info["created_at"]).timestamp() < cutoff
    ]
    for iid in stale_ids:
        del _active_imports[iid]
