"""AI's explicit day-end choices, committed to the existing Open Loop authority.

The diary carries the saved response, not a second concern store. This module
never calls a model. A failed review does not invalidate a successful diary.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .manager import DEFAULT_DB_PATH, OpenLoopStore

log = logging.getLogger(__name__)
CONTRACT = "open-loop-day-review-v3"
SUPPORTED_CONTRACTS = {CONTRACT, "open-loop-day-review-v2", "open-loop-day-review-v1"}
SNAPSHOT_FIELDS = ("id", "content", "status", "wake_at", "updated_at", "last_reinforced_at")
PROGRESS_FIELDS = ("latest_progress", "fact_confirmed_at", "fact_sources_json")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _clock(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("invalid_day_review_time") from None
    zone = ZoneInfo("Asia/Shanghai")
    return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)


def prepare(date, messages, *, db_path=DEFAULT_DB_PATH):
    """Read without constructing a store or waking snoozed concerns."""
    path = Path(db_path).resolve()
    items = []
    if path.exists():
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            items = [dict(r) for r in db.execute(
                "SELECT * FROM open_loops WHERE status IN ('active','snoozed') ORDER BY created_at,id")]
    from .progress import inspect_progress
    inspect_progress(items, path.parent / 'event_chronicle.db')
    refs = {f"O{n}": {**{k: item.get(k, "[]" if k == "fact_sources_json" else "") for k in SNAPSHOT_FIELDS + PROGRESS_FIELDS},
                       'progress_source_current': item.get('progress_source_current', False)}
            for n, item in enumerate(items, 1)}
    sources = {str(m["message_id"]): {
        "digest": _digest(m), "role": m.get("role"), "timestamp": m.get("timestamp", ""),
        "event_type": m.get("event_type", ""),
    } for m in messages if m.get("message_id")}
    from .progress import bind_raw_sources
    raw_available = bind_raw_sources(sources, path.parent / 'event_chronicle.db')
    context = {"contract": CONTRACT, "date": date, "db_path": str(path),
               "refs": refs, "sources": sources, "messages_digest": _digest(messages),
               'raw_authority_available': raw_available}
    context["key"] = _digest({k: v for k, v in context.items() if k != "db_path"})
    return context


def prompt(context):
    lines = ["\n今晚也回看一下还挂在心里的事："]
    for ref, item in context["refs"].items():
        state = "（暂时放着）" if item["status"] == "snoozed" else ""
        lines.append(f"{ref}：{item['content']}{state}")
        if item.get('latest_progress') and item.get('progress_source_current'):
            lines.append(f"最近已知进展（{item.get('fact_confirmed_at', '')}）：{item['latest_progress']}")
        elif item.get('latest_progress'):
            lines.append('此前近况的原话已变更或暂不可核对。')
    if not context["refs"]:
        lines.append("目前没有特意留下的挂念。")
    if context.get('contract') == CONTRACT:
        lines.append("原条目是过去留下的关注。按现在的进展重新看它：仍想关注但关注内容或时点变了，用 revise 写成此刻的挂念；"
                     "reinforce 保留原句，表示其中的关注和安排现在仍适用。已经好转也可以继续关心，改写时说清此刻惦记什么。")
    lines.extend([
        "日记仍写今天已经留下的体验；下面另记你在收尾时的新选择。",
        "对照今天的原话，决定哪些继续惦记、怎样改写、暂放或放下；也可以留下最多三条新的挂念。",
        "事情的进展依据原话和真实回执，你是否继续在意由你自己选择。没再聊到、发过提醒，都不等于事情完成。",
        "同一件事沿原条目更新；无变化可留空，不必把全部挂念每天强化。普通待办不自动变成你的挂念。",
        "在同一 JSON 顶层附 open_loop_updates 数组。每项 action 为 hold/revise/reinforce/snooze/release/update_progress；"
        "已有条目的 ref 填上方编号（如 O1），新增或改写用 content（一句自然短话），reason 简述本次选择。",
        "source_message_ids 引用支持这次选择的今日真实消息。若更新人类伙伴的近况，"
        "fact_source_message_ids 只列直接支持她近况变化的人类伙伴原话；行动回执可作为选择依据，AI 的复述不刷新事实确认时间。",
        "原话提到已有挂念的新进展时，latest_progress 用一句话留下她实际确认的变化，配 fact_source_message_ids；"
        "可以继续惦记已经好转或结束的事，进展仍随原话更新。只更新进展、不改变你的关注选择时用 update_progress。"
        "现实经历与聊天中的想象、角色扮演各按原来的语境记录。",
        "reinforce 是此刻明确继续保留，可不新增事实来源。snooze 用 wake_at 写明再次浮起的绝对时间。"
        "release 表示你放下关注，不替事情宣判完成。没有变化返回 open_loop_updates:[]。",
    ])
    return "\n".join(lines)


def bind_request(context, text, *, body_digest=""):
    """The complete diary input also contains persona, body and prior diary."""
    bound = {**context, "prompt_digest": _digest(text), "body_digest": body_digest}
    bound["key"] = _digest({k: v for k, v in bound.items() if k not in {"db_path", "key"}})
    return bound


def _output_path(context):
    return Path(context["db_path"]).parent / ".open-loop-day-review-outputs" / (context["date"] + "_" + context["key"] + ".json")


def recover(date, messages, *, db_path=DEFAULT_DB_PATH):
    """A backfill restores the original successful response, never a new choice."""
    directory = Path(db_path).resolve().parent / ".open-loop-day-review-outputs"
    candidates = []
    for path in directory.glob(date + "_*.json"):
        saved = json.loads(path.read_text("utf-8"))
        context = saved["context"]
        if context.get("date") != date or context.get("contract") not in SUPPORTED_CONTRACTS:
            raise ValueError("day_review_output_conflict")
        if context["messages_digest"] != _digest(messages):
            raise ValueError("saved_day_review_source_changed")
        candidates.append(saved)
    if len(candidates) > 1:
        raise ValueError("ambiguous_saved_day_review")
    return candidates[0] if candidates else None


def save_response(context, response):
    """Save the complete public return before parsing; no provider internals."""
    path = _output_path(context)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"context": context, "response": response}
    if path.exists():
        if json.loads(path.read_text("utf-8")) != payload:
            raise ValueError("day_review_output_conflict")
        return
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
    temp.replace(path)


def saved_response(context):
    path = _output_path(context)
    if not path.exists():
        return None
    saved = json.loads(path.read_text("utf-8"))
    if saved.get("context") != context:
        raise ValueError("day_review_output_conflict")
    return saved["response"]


def _updates(value, context):
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("invalid_day_review_updates")
    result, touched = [], set()
    if sum(isinstance(item, dict) and item.get("action") == "hold" for item in value) > 3:
        raise ValueError("too_many_new_day_review_concerns")
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid_day_review_item")
        action = item.get("action")
        if action not in {"hold", "revise", "reinforce", "snooze", "release", "update_progress"}:
            raise ValueError("invalid_day_review_action")
        ref = item.get("ref", "")
        # The regular tool also accepts a complete sentence. Recover the same
        # exact identity from this frozen input, never from a semantic guess.
        if action != "hold" and ref == "":
            matches = [key for key, source in context["refs"].items()
                       if isinstance(item.get("content"), str)
                       and item["content"] == source["content"]]
            if len(matches) == 1:
                ref = matches[0]
        if action != "hold" and ref not in context["refs"]:
            raise ValueError("unknown_day_review_ref")
        if ref and (ref in touched or action == "hold"):
            raise ValueError("duplicate_day_review_ref")
        touched.add(ref)
        text, reason = item.get("content", ""), item.get("reason", "")
        if not isinstance(text, str):
            raise ValueError("invalid_day_review_content")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("missing_day_review_choice")
        if action in {"hold", "revise"} and (not isinstance(text, str) or not text.strip() or len(text) > 400):
            raise ValueError("invalid_day_review_content")
        sources = item.get("source_message_ids", [])
        facts = item.get("fact_source_message_ids", [])
        for ids in (sources, facts):
            if not isinstance(ids, list) or any(not isinstance(i, str) or i not in context["sources"] for i in ids):
                raise ValueError("invalid_day_review_source")
        if action in {"hold", "revise"} and not sources:
            raise ValueError("missing_day_review_source")
        if not set(facts) <= set(sources):
            raise ValueError("invalid_day_review_fact_source")
        progress = item.get('latest_progress', '')
        if not isinstance(progress, str) or len(progress) > 600:
            raise ValueError('invalid_day_review_progress')
        progress = progress.strip()
        if progress and not facts:
            raise ValueError('missing_day_review_fact_source')
        if (context.get('contract') in {CONTRACT, 'open-loop-day-review-v2'} and facts and not progress) or (action == 'update_progress' and not progress):
            raise ValueError('missing_day_review_progress')
        for mid in facts:
            source = context["sources"][mid]
            if source["role"] != "user" or source["event_type"]:
                raise ValueError("not_direct_day_review_fact")
            _clock(source["timestamp"])
        wake = item.get("wake_at", "")
        if action == "snooze":
            if not isinstance(wake, str) or not wake:
                raise ValueError("invalid_day_review_wake")
            wake = _clock(wake).isoformat(timespec="seconds")
            if _clock(wake) <= datetime.now(ZoneInfo("Asia/Shanghai")):
                raise ValueError("day_review_wake_not_future")
        result.append({"action": action, "ref": ref, "content": text.strip(), "reason": reason.strip(),
                       "sources": sources, "facts": facts, "wake_at": wake, 'progress': progress})
    return result


def _ensure_receipts(db):
    db.execute("""CREATE TABLE IF NOT EXISTS open_loop_day_reviews (
        review_key TEXT PRIMARY KEY, active_date TEXT NOT NULL UNIQUE,
        diary_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
        error_code TEXT NOT NULL DEFAULT '', recorded_at TEXT NOT NULL)""")


def apply(context, updates, *, diary_id, messages, store, retry_blocked=False):
    """Compare-and-set all choices and the receipt in one local transaction."""
    if context.get("contract") not in SUPPORTED_CONTRACTS:
        raise ValueError("unknown_day_review_contract")
    payload = {"context": context, "updates": updates}
    now = datetime.now().isoformat(timespec="seconds")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _ensure_receipts(db)
        old = db.execute("SELECT * FROM open_loop_day_reviews WHERE active_date=?", (context["date"],)).fetchone()
        if old:
            if (old["review_key"] != context["key"] or old["diary_id"] != diary_id
                    or json.loads(old["payload"]) != payload):
                return {"status": "conflict", "error_code": "day_already_reviewed"}
            if not (retry_blocked and old["status"] == "blocked"
                    and old["error_code"] == "unknown_day_review_ref"):
                return {"status": old["status"], "error_code": old["error_code"], "reused": True}
        error = ""
        try:
            if _digest(messages) != context["messages_digest"]:
                raise ValueError("day_review_source_changed")
            chosen = _updates(updates, context)
            if context.get('raw_authority_available'):
                from .progress import source_snapshot, sources_current
                for item in chosen:
                    if item['facts']:
                        if any('raw_snapshot' not in context['sources'][mid] for mid in item['facts']):
                            raise ValueError('day_review_fact_source_unavailable')
                        proofs = source_snapshot(messages, item['facts'], context=context)
                        if not sources_current(proofs, Path(context['db_path']).parent / 'event_chronicle.db'):
                            raise ValueError('day_review_source_changed')
            new_content = set()
            for item in chosen:
                if item["action"] == "hold":
                    if item["content"] in new_content or db.execute(
                        "SELECT 1 FROM open_loops WHERE content=? AND status IN ('active','snoozed')",
                        (item["content"],),
                    ).fetchone():
                        raise ValueError("duplicate_day_review_concern")
                    new_content.add(item["content"])
                    continue
                before = context["refs"][item["ref"]]
                current = db.execute("SELECT * FROM open_loops WHERE id=?", (before["id"],)).fetchone()
                fields = SNAPSHOT_FIELDS + (PROGRESS_FIELDS if context['contract'] == CONTRACT else ())
                if not current or any(current[k] != before[k] for k in fields):
                    raise ValueError("day_review_loop_changed")
                if context['contract'] != CONTRACT and current['latest_progress']:
                    raise ValueError('day_review_loop_changed')
                if item["facts"]:
                    confirmed = max(_clock(context["sources"][mid]["timestamp"]) for mid in item["facts"])
                    if current["fact_confirmed_at"] and confirmed < _clock(current["fact_confirmed_at"]):
                        raise ValueError("day_review_fact_outdated")
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc) if isinstance(exc, ValueError) else "invalid_day_review_item"
        if old and error:
            # Keep the original blocked receipt. Explicit recovery does not
            # weaken source/snapshot checks or replace its failure history.
            return {"status": "blocked", "error_code": error}
        choice_at = old["recorded_at"] if old else now
        if not error:
            for n, item in enumerate(chosen):
                action = item["action"]
                if action == "hold":
                    loop_id = "loop_" + _digest([context["key"], n])[:12]
                    db.execute("INSERT INTO open_loops (id,content,created_at,updated_at,last_reinforced_at) VALUES (?,?,?,?,?)",
                               (loop_id, item["content"], now, now, now))
                    previous = ""
                else:
                    loop_id = context["refs"][item["ref"]]["id"]
                    previous = context["refs"][item["ref"]]["status"]
                status = previous if action == 'update_progress' else "resolved" if action == "release" else "snoozed" if action == "snooze" else "active"
                if action == "revise":
                    db.execute("UPDATE open_loops SET content=? WHERE id=?", (item["content"], loop_id))
                if action != 'update_progress':
                    db.execute("UPDATE open_loops SET status=?,wake_at=?,updated_at=?,resolved_at=? WHERE id=?",
                               (status, item["wake_at"] if action == "snooze" else "", now,
                                now if action == "release" else "", loop_id))
                if action in {"hold", "revise", "reinforce"}:
                    db.execute("UPDATE open_loops SET last_reinforced_at=?,expired_at='' WHERE id=?", (choice_at, loop_id))
                if item["facts"]:
                    confirmed = max(_clock(context["sources"][mid]["timestamp"]) for mid in item["facts"]).isoformat(timespec="seconds")
                    old_fact = db.execute("SELECT fact_confirmed_at FROM open_loops WHERE id=?", (loop_id,)).fetchone()[0]
                    if not old_fact or _clock(confirmed) >= _clock(old_fact):
                        db.execute("UPDATE open_loops SET fact_confirmed_at=?,fact_source_ids_json=? WHERE id=?",
                                   (confirmed, json.dumps(item["facts"]), loop_id))
                        if item['progress']:
                            from .progress import source_snapshot
                            db.execute('UPDATE open_loops SET latest_progress=?,fact_sources_json=? WHERE id=?',
                                       (item['progress'], json.dumps(source_snapshot(messages, item['facts'], context=context)), loop_id))
                store._append_transition(db, loop_id, action, previous, status,
                                         "day_review:" + context["key"], choice_at)
        status = "blocked" if error else "applied"
        if old:
            db.execute("""CREATE TABLE IF NOT EXISTS open_loop_day_review_recoveries (
                review_key TEXT PRIMARY KEY, original_receipt TEXT NOT NULL,
                recovered_at TEXT NOT NULL)""")
            db.execute("INSERT INTO open_loop_day_review_recoveries VALUES (?,?,?)",
                       (context["key"], json.dumps(dict(old), ensure_ascii=False), now))
            db.execute("UPDATE open_loop_day_reviews SET status=?,error_code=?,recorded_at=? WHERE review_key=?",
                       (status, error, now, context["key"]))
        else:
            db.execute("INSERT INTO open_loop_day_reviews VALUES (?,?,?,?,?,?,?)",
                       (context["key"], context["date"], diary_id, status,
                        json.dumps(payload, ensure_ascii=False), error, now))
    return {"status": status, "error_code": error, **({"recovered": True} if old else {})}


def resume(entry, *, messages, store=None):
    saved = (getattr(entry, "structured_data", None) or {}).get("_open_loop_day_review")
    if not saved:
        return {"status": "not_requested"}
    context = saved["context"]
    if entry.date != context["date"]:
        return {"status": "blocked", "error_code": "day_review_date_changed"}
    store = store or OpenLoopStore(context["db_path"])
    return apply(context, saved.get("updates"), diary_id=entry.id, messages=messages, store=store)


def resume_saved(entry):
    """Recovery consumes only an already-persisted proposal; never generates one."""
    if not (getattr(entry, "structured_data", None) or {}).get("_open_loop_day_review"):
        return {"status": "not_requested"}
    try:
        from event_chronicle import _read_all_messages_for_date
        result = resume(entry, messages=_read_all_messages_for_date(entry.date))
        if result["status"] in {"blocked", "conflict"}:
            log.warning("Open Loop day review kept for inspection: %s", result["error_code"])
        return result
    except Exception:
        log.exception("Open Loop day review deferred; diary remains saved")
        return {"status": "pending", "error_code": "day_review_commit_failed"}
