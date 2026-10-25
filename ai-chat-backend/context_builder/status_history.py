"""Source-preserving status-event projection for message and text consumers."""
from __future__ import annotations

from datetime import datetime

STATUS_MATERIAL_NAME = "mirrow_status_event"


def context_history_start(conversation_messages=(), *, active_date: str | None = None) -> str:
    """Keep cross-midnight active history; do not drop it at calendar midnight."""
    today = datetime.now().strftime("%Y-%m-%dT00:00:00")
    starts = [today]
    if active_date:
        try:
            starts.append(datetime.strptime(active_date, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00"))
        except ValueError:
            pass
    for message in conversation_messages or ():
        meta = getattr(message, "metadata", None) or {}
        stamp = meta.get("timestamp")
        try:
            dt = datetime.fromisoformat(str(stamp))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            starts.append(dt.strftime("%Y-%m-%dT00:00:00"))
        except (ValueError, TypeError):
            continue
    return min(starts)


def compact_status_text(text: str) -> str:
    """Only rewrite the frozen machine header; the entire fact body survives."""
    import re
    header, sep, fact = str(text).partition("\n")
    match = re.fullmatch(r"发生时间：([^；]+)；记录时间：([^；]+)；来源：(.+)。", header)
    if not match or not sep:
        return str(text)
    occurred, recorded, origin = match.groups()
    stamp = occurred.replace("T", " ")[:19]
    if occurred != recorded:
        stamp += "；记录于" + recorded.replace("T", " ")[:19]
    return f"[{stamp}·{origin}] {fact}"


def project_status_history(entries) -> list[dict]:
    """Only new frozen records can enter history; legacy records stay at tail.

    Ordering uses acquisition time, so later reports/corrections never travel
    back to their earlier occurrence time. No live sensor fields enter a fact.
    """
    from wander_manager.user_status import STATUS_DISPLAY_NAMES, UserStatus
    messages = []
    for entry in entries or ():
        frozen = entry.get("context_event_version") == 1 and bool(entry.get("context_text"))
        sort_time = "9999-12-31T23:59:20"
        if frozen:
            try:
                acquired = datetime.fromisoformat(str(entry["recorded_at"]))
                if acquired.tzinfo is not None:
                    acquired = acquired.astimezone().replace(tzinfo=None)
                sort_time = acquired.isoformat()
            except (KeyError, TypeError, ValueError):
                pass  # Invalid/unknown acquisition time cannot backfill history.
            text = compact_status_text(entry["context_text"])
        else:
            stamp = str(entry.get("time", ""))
            stime = stamp[11:16] if "T" in stamp else stamp[:5]
            def label(value):
                try:
                    return STATUS_DISPLAY_NAMES.get(UserStatus(value), value)
                except ValueError:
                    return value
            fact = entry.get("_text") or f"{label(entry.get('from', '?'))} → {label(entry.get('to', '?'))}"
            text = f"[{stime}] 人类伙伴的状态发生了变化：{fact}（旧记录；详细描述与来源未记录）"
        messages.append({
            "role": "user", "name": STATUS_MATERIAL_NAME,
            "content": "[状态记录·MIRROW系统提供，非聊天原话] " + text,
            "_ts": sort_time, "_section": "status_changes",
            "_status_frozen": frozen and sort_time != "9999-12-31T23:59:20",
        })
    return messages


def history_sort_key(message) -> str:
    stamp = str(message.get("_ts", "9999"))
    try:
        dt = datetime.fromisoformat(stamp)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat()
    except ValueError:
        return stamp
