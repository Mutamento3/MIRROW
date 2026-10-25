"""Read-only Wander activity index and domain-routed detail for the bookshelf."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def _connect(path) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _chronicle_db():
    from event_chronicle import get_global_chronicle
    return get_global_chronicle().db_path


def _daily_text(row) -> str:
    from context_builder.wander_event_projection import format_wander_activity_event
    return format_wander_activity_event(
        str(row["content"] or ""), row["tool_calls"], str(row["message_id"] or ""),
        level="daily", timestamp=str(row["timestamp"] or ""),
    )


def _tool_name(row) -> str:
    try:
        calls = json.loads(row["tool_calls"] or "[]")
        return str((calls[0] or {}).get("tool") or "") if calls else ""
    except (TypeError, ValueError):
        return ""


def wander_documents(*, limit: int = 200, date: str = ""):
    """Bounded index documents; full detail is resolved only by read_event."""
    from .bookshelf_search import SearchDocument, sanitize_archive_text
    db = _chronicle_db()
    if not Path(db).exists():
        return []
    where = "event_type='wander_activity'"
    args: list = []
    if date:
        where += " AND active_date=?"
        args.append(date)
    try:
        conn = _connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM conversation_messages WHERE {where} "
            "ORDER BY timestamp DESC LIMIT ?",
            (*args, max(1, min(int(limit or 200), 500))),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    documents = []
    for row in rows:
        text = _daily_text(row)
        if not text.strip():
            continue
        documents.append(SearchDocument(
            id=f"wander:{row['message_id']}", source="wander",
            text=sanitize_archive_text(text, limit=1800),
            date=str(row["active_date"] or str(row["timestamp"] or "")[:10]),
            metadata={"event_id": str(row["message_id"] or ""),
                      "activity_type": _tool_name(row)},
        ))
    return documents


def _materials(row):
    try:
        calls = json.loads(row["tool_calls"] or "[]")
    except (TypeError, ValueError):
        calls = []
    first = calls[0] if isinstance(calls, list) and calls else {}
    extra = first.get("extra_data") if isinstance(first, dict) else {}
    frozen = extra.get("memory_experience") if isinstance(extra, dict) else {}
    materials = frozen.get("materials") if isinstance(frozen, dict) else []
    refs: dict = {}
    for item in materials or []:
        if isinstance(item, dict) and isinstance(item.get("refs"), dict):
            refs = {**refs, **item["refs"]}
    return [item for item in materials or [] if isinstance(item, dict)], refs


def _workshop_details(refs):
    home = Path(os.getenv("MIRROW_WORKSHOP_HOME") or (Path.home() / ".mirrow-workshop"))
    database = home / "workshop.sqlite3"
    if not database.exists():
        return ["工作坊记录暂时不可用（找不到数据目录）。"]
    lines: list[str] = []
    try:
        conn = _connect(database)
        conn.row_factory = sqlite3.Row
        jobs: dict = {}
        for item in (refs.get("jobs") or [])[:8]:
            job_id = str(item.get("id") or "").strip()
            if not job_id:
                continue
            row = conn.execute(
                "SELECT id,title,state,revision,plan_version,confirmed_version,"
                "visibility,updated_at FROM jobs WHERE id=? AND deleted=0", (job_id,),
            ).fetchone()
            if not row or row["visibility"] != "public":
                continue
            jobs[row["id"]] = dict(row)
            lines.append(
                f"工单《{row['title']}》：状态{row['state']}，需求版本{row['revision']}，"
                f"方案版本{row['plan_version']}，已确认版本{row['confirmed_version']}，"
                f"更新于{row['updated_at']}。"
            )
        for item in (refs.get("artifacts") or [])[:8]:
            artifact_id = str(item.get("id") or "").strip()
            if not artifact_id:
                continue
            row = conn.execute(
                "SELECT id,job_id,name,revision,summary FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
            if not row:
                continue
            job = jobs.get(row["job_id"])
            if job is None:
                parent = conn.execute(
                    "SELECT visibility FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
                if not parent or parent["visibility"] != "public":
                    continue
            summary = str(row["summary"] or "").strip()
            if len(summary) > 600:
                summary = "（摘要较长，见作品柜）"
            lines.append(f"作品《{row['name']}》v{row['revision']}：{summary}"
                         if summary else f"作品《{row['name']}》v{row['revision']}。")
        conn.close()
    except sqlite3.Error:
        return ["工作坊记录暂时不可用。"]
    return lines or ["工作坊记录中没有可展开的公开内容。"]


def _domain_detail(item, refs, *, session_id=''):
    kind = item.get("kind")
    if kind == 'xhs_interactions':
        if isinstance(item.get('interaction_snapshot'), dict):
            from xhs_interactions.projection import project
            return [project(item['interaction_snapshot'])]
        from xhs_interactions.archive import detail
        data = detail(refs=item.get('refs') or {}, session_id=session_id)
        return [data['text']]
    if kind == "workshop":
        if refs:
            return _workshop_details(refs)
        return ["工作坊材料：这条活动是旧格式，没有结构化作品引用；"
                "完整内容仍保存在原始活动记录和作品柜中。"]
    if kind == "checkup":
        conclusion = str(item.get("conclusion") or "uncertain")
        execution = str(item.get("execution_status") or "unknown")
        from checkup_assessment import goal_assessment_text
        return list(filter(None, [
            f"查岗来源回执：{item.get('source_status') or conclusion}（执行={execution}）；不代表目标已确认，逐来源明细不回放。",
            goal_assessment_text(item.get('goal_assessment')),
        ]))
    if kind == "game":
        from memory_v2.experience_material import project_material
        fact = project_material(item)
        return [fact] if fact else []
    if kind in {"music", "music_listening_attempt"}:
        title = str(item.get("title") or "").strip()
        artist = str(item.get("artist") or "").strip()
        receipt = item.get("music_execution") or {}
        return ["听歌：" + (f"《{title}》" + (f"（{artist}）" if artist else "")
                            if title else "一首曲目")
                + ("；" + str(receipt.get("fact")) if receipt.get("fact") else "") + "。"]
    if kind == "reading":
        count = str(item.get("displayed_message_count") or "").strip()
        return ["回看历史对话摘录" + (f" {count} 条" if count else "")
                + "；原文在历史对话。"]
    if kind in {"xhs", "xhs_publication", "shopping"}:
        title = str(item.get("title") or "").strip()
        return [f"来源材料" + (f"《{title}》" if title else "")
                + "：完整内容保留在对应来源库。"]
    description = str(item.get("description") or "").strip()
    if description and len(description) <= 600:
        return [description]
    return [f"该材料类型（{kind or '未标注'}）未展开；完整内容保留在对应来源库。"]


def read_event(event_id: str):
    """Return the bounded daily view plus domain-owned detail for one event."""
    db = _chronicle_db()
    if not Path(db).exists():
        return {"status": "unavailable", "message": "活动档案暂时不可用。"}
    try:
        conn = _connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM conversation_messages WHERE message_id=? "
            "AND event_type='wander_activity' LIMIT 1", (str(event_id or ""),),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return {"status": "unavailable", "message": "活动档案暂时不可用。"}
    if not row:
        return {"status": "not_found", "message": "没有找到这条活动记录。"}
    summary = _daily_text(row)
    materials, refs = _materials(row)
    details: list[str] = []
    for item in materials:
        details.extend(_domain_detail(item, refs, session_id=str(row['session_id'] or '')))
    text = "\n".join(part for part in [summary, *details] if part)
    return {"status": "ok", "event_id": str(event_id),
            "timestamp": str(row["timestamp"] or ""), "text": text, "refs": refs}


__all__ = ["wander_documents", "read_event"]
