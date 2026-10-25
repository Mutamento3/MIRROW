"""Deterministic compact history for proactive messages.

Only WANDER_PUSH/SENTINEL_PUSH/REMINDER_PUSH use this module.  FULL_CHAT keeps
the existing complete-yesterday path.  There is no model call here: the
Memory V2 day compacts are the factual skeleton while
``conversation_messages`` remains the authoritative source for every raw
excerpt.  Pre-V2 days retain the structured-diary fallback.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from diary_structured import structured_data_is_usable
from context_builder.event_history_adapter import adapt_history_for_context


PROACTIVE_RECIPES = frozenset({"WANDER_PUSH", "SENTINEL_PUSH", "REMINDER_PUSH"})
SCENE_GAP_SECONDS = 2 * 60 * 60
SCENE_MAX_MESSAGES = 12
SCENE_MAX_CHARS = 4200
RELATED_MAX_EVENTS = 2
RELATED_MAX_CHARS = 2600
TIMELINE_MAX_CHARS = 3600
MESSAGE_MAX_CHARS = 700


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        text = str(value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _timestamp_label(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        return text[11:16]
    return text[:16]


def _message_id(message: Dict[str, Any]) -> str:
    return str(message.get("message_id") or message.get("id") or "").strip()


def _role_label(message: Dict[str, Any]) -> str:
    if message.get('event_type') == 'model_switch':
        return '运行事件'
    role = str(message.get("role") or "")
    if role == "user":
        return "人类伙伴"
    if message.get("is_wander"):
        return "AI·漫想"
    if message.get("is_sentinel"):
        return "AI·哨兵"
    if message.get("is_reminder"):
        return "AI·提醒"
    if role == "system":
        return "系统"
    return "AI"


def _normalise_messages(messages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from api_sources.model_context import enrich_rows
    from model_provenance import annotate_history
    result = []
    seen = set()
    for raw in adapt_history_for_context(enrich_rows([item for item in messages or [] if isinstance(item, dict)])):
        if not isinstance(raw, dict):
            continue
        if not str(raw.get("content") or "").strip():
            continue
        item = annotate_history(raw)
        item["timestamp"] = str(raw.get("timestamp") or "")
        key = _message_id(item) or (item["timestamp"], item.get("role"), item.get("content"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(key=lambda item: (_parse_ts(item.get("timestamp")) or datetime.min, _message_id(item)))
    return result


def _timeline_items(structured: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = structured.get("factual_timeline") if isinstance(structured, dict) else []
    return [item for item in items if isinstance(item, dict) and str(item.get("event") or "").strip()]


def _timeline_text(items: Sequence[Dict[str, Any]]) -> str:
    lines = ["## 昨日事实时间线（Memory V2 记忆盒，按发生顺序）"]
    if not items:
        lines.append("（昨日没有结构化事实事件）")
    for item in items:
        ts = str(item.get("time") or "未知时间").strip()
        event = str(item.get("event") or "").strip()
        lines.append(f"- {ts}：{event}")
    text = "\n".join(lines)
    return text[:TIMELINE_MAX_CHARS]


def _last_continuous_scene(messages: Sequence[Dict[str, Any]]) -> tuple[str, Set[str]]:
    """Return the tail scene, walking backwards until a two-hour gap."""
    if not messages:
        return "", set()
    selected: List[Dict[str, Any]] = [messages[-1]]
    # Compare each older message with the newer message already at the tail;
    # the previous implementation paired identical items and therefore never
    # detected a real gap.
    for current, previous in zip(reversed(messages[:-1]), reversed(messages[1:])):
        cur_dt = _parse_ts(current.get("timestamp"))
        prev_dt = _parse_ts(previous.get("timestamp"))
        if cur_dt and prev_dt and (prev_dt - cur_dt).total_seconds() > SCENE_GAP_SECONDS:
            break
        selected.append(current)
        if len(selected) >= SCENE_MAX_MESSAGES:
            break
    selected.reverse()
    lines = ["## 昨晚最后一个连续场景（权威原文）"]
    source_ids: Set[str] = set()
    for message in selected:
        mid = _message_id(message)
        if mid:
            source_ids.add(mid)
        content = str(message.get("content") or "").strip()[:MESSAGE_MAX_CHARS]
        lines.append(f"- {_timestamp_label(message.get('timestamp'))} {_role_label(message)}：{content}")
    text = "\n".join(lines)
    if len(text) > SCENE_MAX_CHARS:
        text = text[:SCENE_MAX_CHARS].rstrip() + "…"
    return text, source_ids


def _query_terms(value: Any) -> List[str]:
    text = str(value or "").lower()
    terms = re.findall(r"[a-z0-9_]{2,}|[\u3400-\u9fff]{2,}", text)
    # For Chinese, include short n-grams so a query embedded in a longer
    # phrase can match without a tokenizer dependency.
    chars = re.sub(r"[^\u3400-\u9fff]", "", text)
    terms.extend(chars[i:i + 2] for i in range(max(0, len(chars) - 1)))
    terms.extend(chars[i:i + 3] for i in range(max(0, len(chars) - 2)))
    seen = set()
    return [term for term in terms if term and not (term in seen or seen.add(term))]


def _related_text(
    items: Sequence[Dict[str, Any]],
    messages: Sequence[Dict[str, Any]],
    query: str,
    scene_ids: Set[str],
) -> str:
    terms = _query_terms(query)
    if not terms:
        return ""
    ranked = []
    for index, item in enumerate(items):
        event = str(item.get("event") or "")
        haystack = (event + " " + str(item.get("time") or "")).lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, -index, item))
    ranked.sort(reverse=True, key=lambda row: (row[0], row[1]))
    message_by_id = {_message_id(message): message for message in messages if _message_id(message)}
    lines = ["## 与本次触发相关的昨日原文（确定性匹配）"]
    used: Set[str] = set(scene_ids)
    event_count = 0
    for _, _, item in ranked:
        if event_count >= RELATED_MAX_EVENTS:
            break
        fragments = []
        for raw_id in item.get("source_message_ids", []) or []:
            mid = str(raw_id).strip()
            if not mid or mid in used or mid not in message_by_id:
                continue
            message = message_by_id[mid]
            used.add(mid)
            fragments.append(
                f"- {_timestamp_label(message.get('timestamp'))} {_role_label(message)}："
                f"{str(message.get('content') or '').strip()[:MESSAGE_MAX_CHARS]}"
            )
        if fragments:
            lines.extend(fragments)
            event_count += 1
    if event_count == 0:
        return ""
    return "\n".join(lines)[:RELATED_MAX_CHARS]


def build_compact_yesterday_context(
    chronicle: Any,
    target_date: str,
    context_query: str = "",
    messages: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Build compact proactive history, or ``None`` when unsafe/unavailable.

    ``None`` is intentional: callers must then inject their existing complete
    raw-yesterday fallback.  A parse-failed or missing diary is never silently
    rendered as an empty proactive history.
    """
    try:
        raw_messages = list(messages) if messages is not None else chronicle.get_messages_by_date(target_date)
        raw_messages = _normalise_messages(raw_messages)
        # A model-produced source id is only an index hint.  Validate every
        # reference against SQLite-authoritative messages before it can enter
        # compact history; hallucinated anchors make the compact layer unsafe.
        valid_message_ids = {_message_id(message) for message in raw_messages if _message_id(message)}
        timeline_source = "memory_v2"
        try:
            from memory_v2.day_projection import load_day_compact_projection
            day_projection = load_day_compact_projection(target_date)
        except Exception:
            day_projection = None
        if day_projection:
            raw_timeline = [
                {
                    "time": _timestamp_label(item.get("start_ts")),
                    "event": str(item.get("summary") or "").strip(),
                    "source_message_ids": list(item.get("message_ids") or []),
                }
                for item in day_projection.get("items", [])
                if str(item.get("summary") or "").strip()
            ]
        else:
            entry = chronicle.get_diary_entry_by_date(target_date) if chronicle else None
            structured = getattr(entry, "structured_data", None) if entry else None
            if not structured_data_is_usable(structured):
                return None
            if isinstance(structured, str):
                import json
                structured = json.loads(structured)
            raw_timeline = _timeline_items(structured)
            timeline_source = "legacy_diary"

        timeline = []
        for raw_item in raw_timeline:
            valid_ids = [
                str(source_id).strip()
                for source_id in raw_item.get("source_message_ids", []) or []
                if str(source_id).strip() in valid_message_ids
            ]
            if not valid_ids:
                continue
            item = dict(raw_item)
            item["source_message_ids"] = valid_ids
            timeline.append(item)
        if not timeline:
            return None
        timeline_text = _timeline_text(timeline)
        scene_text, scene_ids = _last_continuous_scene(raw_messages)
        related = _related_text(timeline, raw_messages, context_query, scene_ids)
        parts = [timeline_text]
        if scene_text:
            parts.append(scene_text)
        used_ids = set(scene_ids)
        for item in timeline:
            used_ids.update(str(x).strip() for x in item.get("source_message_ids", []) if str(x).strip())
        return {
            "text": "\n\n".join(part for part in parts if part).strip(),
            "related_text": related,
            "timeline": timeline,
            "scene_source_ids": sorted(scene_ids),
            "source_ids": sorted(used_ids),
            "structured_status": "ok",
            "timeline_source": timeline_source,
        }
    except Exception:
        return None


__all__ = ["PROACTIVE_RECIPES", "build_compact_yesterday_context"]
