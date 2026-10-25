"""开放线索的上下文投影选择器。

``open_loops.db`` 是权威业务状态；这里仅读取它，并在 ``affect.db``
保存 attention projection。选择/预算审计不会删除、resolve 或唤醒权威记录。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .store import AffectStore, get_affect_store, now_iso


def _tokens(text: str) -> set[str]:
    result: set[str] = set()
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]+", (text or "").lower()):
        result.add(token)
        if all("\u4e00" <= ch <= "\u9fff" for ch in token) and len(token) >= 2:
            result.update(token[i:i + 2] for i in range(len(token) - 1))
    return result


def _due(wake_at: str, now: str) -> bool:
    if not wake_at:
        return False
    try:
        wake = datetime.fromisoformat(wake_at)
        current = datetime.fromisoformat(now)
        # The legacy open_loops store uses naive local ISO timestamps while
        # affect.db uses offset-aware timestamps.  Treat a naive value as the
        # other side's local wall-clock zone instead of silently missing a due
        # snooze (or raising on a mixed comparison).
        if wake.tzinfo is None and current.tzinfo is not None:
            wake = wake.replace(tzinfo=current.tzinfo)
        elif current.tzinfo is None and wake.tzinfo is not None:
            current = current.replace(tzinfo=wake.tzinfo)
        return wake <= current
    except (TypeError, ValueError):
        return False


def _attention(item: dict[str, Any], sidecar: Optional[dict[str, Any]], now: str) -> tuple[str, str]:
    lifecycle = str(item.get("lifecycle_state") or "").strip()
    if lifecycle == "fading":
        return "fading", "single_fading_resurfacing"
    if sidecar:
        state = str(sidecar.get("state") or "").strip()
        if state in {"foreground", "background", "resolved"}:
            return state, "sidecar_attention"
        if state == "snoozed":
            return ("foreground", "snooze_due") if _due(sidecar.get("wake_at", ""), now) else ("snoozed", "sidecar_snoozed")
    if lifecycle in {"foreground", "background"}:
        return lifecycle, f"lifecycle_{lifecycle}"
    return ("foreground", "legacy_active") if item.get("status") == "active" else ("snoozed", "authoritative_snoozed")


def select_open_loops(
    *,
    recipe: str,
    user_message: str = "",
    mode: str = "shadow",
    char_budget: int = 3200,
    foreground_limit: int = 6,
    related_limit: int = 3,
    due_limit: int = 2,
    store: Optional[AffectStore] = None,
    trace_id: str = "",
    now: str = "",
    record_audit: bool = True,
    turn_id: str = "",
) -> dict[str, Any]:
    """选择本轮 prompt 可展开的线索，并返回 selected/omitted 原因。"""
    affect_store = store or get_affect_store()
    now_value = now or now_iso()
    try:
        from open_loops import get_open_loop_store
        items = get_open_loop_store().list_for_context(turn_id=turn_id)
    except Exception as exc:
        if record_audit:
            affect_store.record_context_audit(
                recipe=recipe, mode=mode, selected=[], omitted=[{"reason": "store_error", "error": type(exc).__name__}],
                injected=False, trace_id=trace_id,
            )
        return {"text": "", "selected": [], "omitted": [{"reason": "store_error"}], "audit_id": ""}

    sidecars = {item.get("loop_id"): item for item in affect_store.list_loop_attention()}
    active: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for item in items:
        loop_id = str(item.get("id") or "").strip()
        if not loop_id:
            continue
        status = str(item.get("status") or "")
        if status in {"resolved", "expired"}:
            omitted.append({"loop_id": loop_id, "reason": status})
            continue
        state, state_reason = _attention(item, sidecars.get(loop_id), now_value)
        if state == "resolved":
            omitted.append({"loop_id": loop_id, "reason": "sidecar_resolved"})
            continue
        if state == "snoozed":
            if _due((sidecars.get(loop_id) or {}).get("wake_at", ""), now_value):
                state, state_reason = "foreground", "snooze_due"
            else:
                omitted.append({"loop_id": loop_id, "reason": "snoozed_not_due"})
                continue
        item = dict(item)
        item["attention_state"] = state
        item["attention_reason"] = state_reason
        active.append(item)

    foreground = [item for item in active if item.get("attention_state") == "foreground"]
    background = [item for item in active if item.get("attention_state") == "background"]
    fading = [item for item in active if item.get("attention_state") == "fading"]
    query_tokens = _tokens(user_message)
    related: list[tuple[int, dict[str, Any]]] = []
    for item in background:
        score = len(query_tokens & _tokens(str(item.get("content") or "")))
        if score:
            related.append((score, item))
        else:
            omitted.append({"loop_id": item.get("id"), "reason": "background_not_related"})
    related.sort(key=lambda pair: (-pair[0], str(pair[1].get("updated_at") or pair[1].get("created_at") or "")))

    due_items = [item for item in active if item.get("attention_reason") == "snooze_due"]
    candidates: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for item in fading[:1]:
        candidates.append((item, "fading"))
        seen.add(str(item.get("id")))
    for item in due_items[: max(0, int(due_limit))]:
        candidates.append((item, "snooze_due"))
        seen.add(str(item.get("id")))
    for item in foreground[: max(0, int(foreground_limit))]:
        if str(item.get("id")) not in seen:
            candidates.append((item, "foreground"))
            seen.add(str(item.get("id")))
    for _, item in related[: max(0, int(related_limit))]:
        if str(item.get("id")) not in seen:
            candidates.append((item, "background_related"))
            seen.add(str(item.get("id")))

    selected: list[dict[str, Any]] = []
    used_chars = 0
    lines: list[str] = ["你此刻仍挂在心里的事："]
    used_chars = len(lines[0])
    for item, reason in candidates:
        loop_id = str(item.get("id"))
        content = str(item.get("content") or "").strip()
        from open_loops.presentation import concern_line
        line = concern_line(item, fading=reason == "fading")
        if used_chars + len(line) + 1 > max(0, int(char_budget)):
            omitted.append({"loop_id": loop_id, "reason": "budget_exceeded", "budget": int(char_budget)})
            continue
        selected.append({"loop_id": loop_id, "content": content, "reason": reason, "chars": len(line)})
        lines.append(line)
        used_chars += len(line) + 1

    selected_ids = {item["loop_id"] for item in selected}
    for item in active:
        loop_id = str(item.get("id"))
        if loop_id not in selected_ids and not any(entry.get("loop_id") == loop_id for entry in omitted):
            omitted.append({"loop_id": loop_id, "reason": "selection_limit"})
    if len(active) > len(selected):
        lines.append(f"另有 {len(active) - len(selected)} 件挂念这次没有展开。")

    text = "\n".join(lines) if selected else ""
    injected = mode == "enabled" and bool(text)
    audit = affect_store.record_context_audit(
        recipe=recipe, mode=mode, selected=selected, omitted=omitted,
        open_loop_chars=len(text), injected=injected, trace_id=trace_id,
    ) if record_audit else {}
    return {
        "text": text if mode == "enabled" else "",
        "preview_text": text,
        "selected": selected,
        "omitted": omitted,
        "audit_id": audit.get("audit_id", ""),
        "injected": injected,
        "mode": mode,
        "total_active": len(active),
    }


__all__ = ["select_open_loops"]
