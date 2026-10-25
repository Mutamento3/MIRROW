"""Build AI's bounded, gradually fading memory of the last active day.

The current active day remains authoritative raw context.  This module only
projects the immediately preceding active day from the existing diary, scene
index and conversation ledger.  It performs no model calls and writes no
state; older material remains available through Memory V2 recall.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from message_roles import EVENT_ROLE, NOTIFICATION_ROLE
from time_utils import TIMEZONE, now_iso, parse_ts
from material_time import local_source_clock, source_range


FULL_SCENE_HOURS = 24.0
ANCHOR_SCENE_HOURS = 36.0
DIARY_HOURS = 48.0
DEFAULT_RAW_BUDGET = 3000
DEFAULT_DIARY_CAP = 1400
DEFAULT_COMPACT_BUDGET = 5200
ANCHOR_MESSAGE_CAP = 1000


def _daily_decay_clock(value: Optional[datetime] = None) -> datetime:
    """Return a day-stable clock for cacheable cross-day projections.

    Cross-day material lives in the stable prompt prefix.  Evaluating scene
    age against the wall clock made a scene switch from full text to anchors
    at an arbitrary hour, invalidating the prefix mid-day.  The local day
    boundary is the intended decay cadence, so every build on the same day
    must observe the same clock.
    """
    current = value or parse_ts(now_iso())
    local_zone = ZoneInfo(TIMEZONE)
    # Builder passes local datetime.now(), while source timestamps have an
    # offset. Keep both on the same local day before computing scene age.
    current = (current.replace(tzinfo=local_zone) if current.tzinfo is None
               else current.astimezone(local_zone))
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _local_hhmm(value: Any) -> str:
    try:
        return parse_ts(str(value or "")).astimezone(ZoneInfo(TIMEZONE)).strftime("%H:%M")
    except Exception:
        return str(value or "")[11:16] or "??:??"


def _human_date(value: str) -> str:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%Y年%m月%d日")
    except (TypeError, ValueError):
        return str(value or "")[:10]


def _content(message: Dict[str, Any], *, cap: int = 0) -> str:
    text = str(message.get("content") or "").strip()
    if cap > 0 and len(text) > cap:
        return text[:cap].rstrip() + "……"
    return text


def _message_line(message: Dict[str, Any], *, cap: int = 0) -> str:
    role = str(message.get("role") or "")
    if role == EVENT_ROLE:
        from context_builder.event_history_adapter import project_event_for_context

        fact = project_event_for_context(message)
        return f"{_local_hhmm(message.get('timestamp'))} 当时的事件：{fact['content']}" if fact else ""
    content = _content(message, cap=cap)
    if not content:
        return ""
    if role == NOTIFICATION_ROLE or role == "divider":
        return ""
    if role == "user":
        speaker = "人类伙伴当时说"
    elif message.get("is_wander"):
        speaker = "你当时主动发给人类伙伴"
    elif message.get("is_sentinel"):
        speaker = "哨兵当时记录"
    elif message.get("is_reminder"):
        speaker = "当时的提醒"
    else:
        speaker = "你当时回应"
    return f"{_local_hhmm(message.get('timestamp'))} {speaker}：{content}"


def _render_messages(messages: Iterable[Dict[str, Any]], *, cap: int = 0) -> str:
    lines, previous_date = [], ''
    for message in messages:
        line = _message_line(message, cap=cap)
        if not line:
            continue
        source_date = local_source_clock(message.get('timestamp'))[:10]
        if source_date and source_date != previous_date:
            lines.append(f'[{source_date}]')
            previous_date = source_date
        lines.append(line)
    return "\n".join(lines)


def _following_assistant(messages: List[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
    for candidate in messages[index + 1:]:
        role = str(candidate.get("role") or "")
        if role == "user":
            return None
        if role == "assistant" and _content(candidate):
            return candidate
    return None


def _anchor_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean = [
        message for message in messages
        if str(message.get("role") or "") not in {NOTIFICATION_ROLE, EVENT_ROLE, "divider"}
        and _content(message)
    ]
    user_indices = [index for index, message in enumerate(clean) if message.get("role") == "user"]
    if not user_indices:
        return clean[-2:]

    chosen: List[Dict[str, Any]] = []
    for index in dict.fromkeys((user_indices[0], user_indices[-1])):
        chosen.append(clean[index])
        reply = _following_assistant(clean, index)
        if reply is not None:
            chosen.append(reply)

    # Preserve chronology and identity while keeping a scene projection small.
    positions = {id(message): index for index, message in enumerate(clean)}
    chosen = list({id(message): message for message in chosen}.values())
    chosen.sort(key=lambda message: positions[id(message)])
    return chosen[:6]


def _scene_age_hours(scene: Dict[str, Any], now: datetime) -> float:
    end = parse_ts(str(scene.get("end_ts") or ""))
    current = now
    if current.tzinfo is None:
        current = current.replace(tzinfo=end.tzinfo)
    return max(0.0, (current - end).total_seconds() / 3600.0)


def _scene_label(scene: Dict[str, Any], *, anchors: bool) -> str:
    clock = source_range(scene.get('start_ts'), scene.get('end_ts'))
    if anchors:
        return f"[更早一些，你还记得这几句｜{clock}]"
    return f"[最近一个活跃日里仍清晰的一段｜{clock}]"


def _bounded_scene_block(
    scene: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    anchors: bool,
    remaining: int,
    included_ids=None,
) -> str:
    if remaining <= 0:
        return ""
    if not anchors:
        full_body = _render_messages(messages)
        full_block = f"{_scene_label(scene, anchors=False)}\n{full_body}" if full_body else ""
        if full_block and len(full_block) <= remaining:
            if included_ids is not None:
                included_ids.update(str(m.get('message_id') or m.get('id') or '')
                                    for m in messages if _message_line(m))
            return full_block
    anchor_body = _render_messages(_anchor_messages(messages), cap=ANCHOR_MESSAGE_CAP)
    anchor_block = f"{_scene_label(scene, anchors=True)}\n{anchor_body}" if anchor_body else ""
    return anchor_block if anchor_block and len(anchor_block) <= remaining else ""


def _build_legacy_cross_day_memory_context(
    chronicle,
    active_date: str,
    *,
    now: Optional[datetime] = None,
    session_id: str = "",
    raw_budget: int = DEFAULT_RAW_BUDGET,
    diary_cap: int = DEFAULT_DIARY_CAP,
    included_ids=None,
) -> str:
    """Return the pre-Memory-V2 diary/scene fallback without model calls."""
    del session_id  # Kept for scheduler/API compatibility; the active day is authoritative.
    active_date = str(active_date or "")[:10]
    if not active_date:
        return ""
    current = _daily_decay_clock(now)

    from scene_manager.scene import get_scene_messages, get_scenes_for_day, segment_messages

    scenes = get_scenes_for_day(active_date, chronicle=chronicle)
    if not scenes:
        # Older installations may not have materialized scene rows yet.  Build
        # the same boundaries in memory without changing the authority DB.
        scenes = segment_messages(chronicle.get_messages_by_date(active_date) or [])
    if not scenes:
        return ""
    scenes = sorted(scenes, key=lambda scene: (parse_ts(scene.get("start_ts", "")), scene.get("id", "")))
    latest_age = min((_scene_age_hours(scene, current) for scene in scenes), default=float("inf"))
    if latest_age > DIARY_HOURS:
        return ""

    blocks: List[str] = []
    diary = chronicle.get_diary_entry_by_date(active_date)
    diary_text = str(getattr(diary, "content", "") or "").strip()
    if diary_text:
        if diary_cap > 0 and len(diary_text) > diary_cap:
            diary_text = diary_text[:diary_cap].rstrip() + "……"
        blocks.append(f"[你对最近一个活跃日的整体记忆｜{_human_date(active_date)}]\n{diary_text}")

    eligible = []
    for scene in scenes:
        age = _scene_age_hours(scene, current)
        if age <= FULL_SCENE_HOURS:
            eligible.append((scene, False))
        elif age <= ANCHOR_SCENE_HOURS:
            eligible.append((scene, True))

    # Prefer actual exchanges over autonomous push-only scenes, and the newest
    # exchanges over older ones.  Diary prose preserves the day's overall arc.
    user_scenes = [item for item in eligible if int(item[0].get("user_msg_count") or 0) > 0]
    candidates = user_scenes or eligible[-1:]
    candidates = sorted(
        candidates,
        key=lambda item: (parse_ts(item[0].get("end_ts", "")), item[0].get("id", "")),
        reverse=True,
    )

    remaining = max(0, int(raw_budget or 0))
    selected: List[tuple[Dict[str, Any], str]] = []
    for scene, anchors in candidates:
        messages = get_scene_messages(scene, chronicle=chronicle)
        block = _bounded_scene_block(scene, messages, anchors=anchors, remaining=remaining,
                                     included_ids=included_ids)
        if not block:
            continue
        selected.append((scene, block))
        remaining -= len(block)
        if remaining <= 0:
            break

    selected.sort(key=lambda item: (parse_ts(item[0].get("start_ts", "")), item[0].get("id", "")))
    blocks.extend(block for _, block in selected)
    return "\n\n".join(blocks).strip()


def _truncate_summary(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    boundary = max(
        (text.rfind(mark, 0, limit) for mark in ("。", "！", "？", "；", "\n")),
        default=-1,
    )
    if boundary >= max(40, int(limit * 0.55)):
        return text[:boundary + 1].rstrip()
    return text[: max(0, limit - 1)].rstrip() + "…"


def _compact_label(item: Dict[str, Any]) -> str:
    clock = source_range(item.get('start_ts'), item.get('end_ts'))
    return f"[{clock} 的记忆梗概]"


def _diary_recollection_source_ids(diary: Any) -> set[str]:
    structured = getattr(diary, "structured_data", None) or {}
    result: set[str] = set()
    if not isinstance(structured, dict):
        return result
    for item in structured.get("k_recollections", []) or []:
        if not isinstance(item, dict):
            continue
        result.update(
            str(source_id).strip()
            for source_id in item.get("source_message_ids", []) or []
            if str(source_id).strip()
        )
    return result


def _select_vivid_items(
    items: List[Dict[str, Any]],
    recollection_ids: set[str],
    *,
    limit: int,
) -> set[int]:
    ranked = []
    for item in items:
        if not item.get("message_ids"):
            continue
        recollection_overlap = bool(recollection_ids & set(item.get("message_ids") or []))
        score = float(item.get("vividness") or 0.0) + (1.25 if recollection_overlap else 0.0)
        ranked.append((score, str(item.get("end_ts") or ""), int(item.get("order") or 0)))
    ranked.sort(reverse=True)
    return {order for _, _, order in ranked[: max(0, int(limit))]}


def _bounded_item_source_block(
    item: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    anchors: bool,
    remaining: int,
    included_ids=None,
) -> str:
    if remaining <= 0 or not messages:
        return ""
    clock = source_range(item.get('start_ts'), item.get('end_ts'))
    if not anchors:
        body = _render_messages(messages)
        block = f"[这盒仍保留着清晰原话｜{clock}]\n{body}" if body else ""
        if block and len(block) <= remaining:
            if included_ids is not None:
                included_ids.update(str(m.get('message_id') or m.get('id') or '')
                                    for m in messages if _message_line(m))
            return block
    body = _render_messages(_anchor_messages(messages), cap=ANCHOR_MESSAGE_CAP)
    block = f"[这盒只剩几句清晰原话｜{clock}]\n{body}" if body else ""
    return block if block and len(block) <= remaining else ""


def _build_compact_cross_day_memory_context(
    chronicle,
    active_date: str,
    projection: Dict[str, Any],
    *,
    now: datetime,
    raw_budget: int,
    diary_cap: int,
    compact_budget: int,
    included_ids=None,
) -> str:
    items = [dict(item) for item in projection.get("items", []) if item.get("summary")]
    from time_utils import parse_ts
    items.sort(key=lambda item: (parse_ts(item.get("start_ts") or ""), int(item.get("order") or 0)))
    if not items:
        return ""
    latest_ts = max((str(item.get("end_ts") or "") for item in items), default="")
    try:
        latest_age = max(0.0, (now - parse_ts(latest_ts)).total_seconds() / 3600.0)
    except Exception:
        latest_age = float("inf")
    if latest_age > DIARY_HOURS:
        return ""

    diary = chronicle.get_diary_entry_by_date(active_date)
    diary_text = str(getattr(diary, "content", "") or "").strip()
    recollection_ids = _diary_recollection_source_ids(diary)

    if latest_age > ANCHOR_SCENE_HOURS:
        # The final fading day keeps only the most strongly retained factual
        # boxes plus AI's subjective epilogue.  It no longer carries raw chat.
        selected_orders = {
            int(item.get("order") or 0)
            for item in sorted(
                items,
                key=lambda item: (
                    float(item.get("vividness") or 0.0),
                    str(item.get("end_ts") or ""),
                ),
                reverse=True,
            )[:3]
        }
        items = [item for item in items if int(item.get("order") or 0) in selected_orders]
        items.sort(key=lambda item: (str(item.get("start_ts") or ""), int(item.get("order") or 0)))
        raw_budget = 0
        compact_budget = min(compact_budget, 2200)

    introduction = (
        f"以下是你对{_human_date(active_date)}从早到晚留下的记忆：不清晰的部分保留为梗概，"
        "仍鲜明的局部保留原话，最后是你在一天结束后写下的感受。"
    )
    # Complete factual impressions take priority over optional vivid excerpts.
    # Borrow only within the existing whole-section allocation, not above it.
    summary_cost = len(introduction) + 2 + sum(
        len(_compact_label(item)) + 1 + len(str(item['summary']).strip()) + 2 for item in items)
    borrowed = min(max(0, summary_cost - compact_budget), max(0, raw_budget))
    compact_budget += borrowed
    raw_budget -= borrowed
    from .cross_day_selection import select_summary_items
    items, summary_blocks, coverage_note = select_summary_items(
        items, budget=max(0, compact_budget - len(introduction) - 2),
        label=_compact_label, recollection_ids=recollection_ids,
    )
    if not items:
        return ""
    vivid_orders = _select_vivid_items(
        items,
        recollection_ids,
        limit=2 if raw_budget > 0 else 0,
    )
    blocks: List[str] = []
    if coverage_note:
        blocks.append(coverage_note)
    remaining_raw = max(0, int(raw_budget or 0))
    used_message_ids: set[str] = set()
    anchors = latest_age > FULL_SCENE_HOURS
    for item in items:
        blocks.append(summary_blocks[item['order']])
        if (int(item.get("order") or 0) not in vivid_orders and not item.get('raw_required')) or remaining_raw <= 0:
            if item.get('raw_required'):
                blocks.append('这段原话本次未展开。')
            continue
        messages = [
            message
            for message_id in item.get("message_ids", []) or []
            if str(message_id) not in used_message_ids
            if (message := chronicle.get_message_by_msg_id(str(message_id))) is not None
        ]
        messages.sort(key=lambda message: str(message.get("timestamp") or ""))
        raw_block = _bounded_item_source_block(
            item,
            messages,
            anchors=anchors,
            remaining=max(0, remaining_raw - 2),
            included_ids=used_message_ids,
        )
        if raw_block:
            blocks.append(raw_block)
            remaining_raw -= len(raw_block) + 2
        elif item.get('raw_required'):
            blocks.append('这段原话本次未展开。')
    if included_ids is not None:
        included_ids.update(used_message_ids)

    diary_label = f"[一天结束后，你留下的主观感受｜{_human_date(active_date)}]\n"
    if diary_text and diary_cap > len(diary_label) + 2:
        diary_text = _truncate_summary(diary_text, diary_cap - len(diary_label) - 2)
        blocks.append(diary_label + diary_text)
    if not blocks:
        return ""
    return (
        introduction + "\n\n"
        + "\n\n".join(blocks)
    ).strip()


def build_cross_day_memory_context(
    chronicle,
    active_date: str,
    *,
    now: Optional[datetime] = None,
    session_id: str = "",
    raw_budget: int = DEFAULT_RAW_BUDGET,
    diary_cap: int = DEFAULT_DIARY_CAP,
    compact_budget: int = DEFAULT_COMPACT_BUDGET,
    included_ids=None,
) -> str:
    """Return a bounded chronological projection of one preceding active day."""

    del session_id  # The active day is authoritative across the fixed main session.
    active_date = str(active_date or "")[:10]
    if not active_date:
        return ""
    current = _daily_decay_clock(now)
    try:
        from memory_v2.day_projection import load_day_compact_projection
        from memory_v2.conversation_source import ConversationSource
        authority_source = ConversationSource(chronicle.db_path, canonical_sessions_only=True)
        projection = load_day_compact_projection(active_date, authority_source=authority_source)
        from memory_v2.scene_planner import scene_start_date
        start = scene_start_date()
        if start and active_date >= start and active_date < current.date().isoformat():
            from memory_v2.scene_material import read_scene_material, cross_day_projection
            scenes = read_scene_material(authority_source, active_date,
                                         completed_before=current.date().isoformat(),
                                         settled_projection=projection)
            # Semantic box impressions are complete; day_compact can be selective.
            from memory_v2.scene_corrections import corrected_cross_day_projection
            projection = corrected_cross_day_projection(scenes, projection) or cross_day_projection(scenes) or projection
        if not projection:
            from memory_v2.scene_planner import SceneReplayPlanner, scene_start_date
            from memory_v2.scene_digest_store import SceneDigestStore, cache_path
            start = scene_start_date()
            if start and active_date >= start and active_date < current.date().isoformat():
                from scene_manager.scene import configured_gap_minutes
                pending = SceneDigestStore(cache_path(authority_source)).projection(
                    SceneReplayPlanner(authority_source, gap_minutes=configured_gap_minutes()),
                    active_date, completed_before=current.date().isoformat(),
                )
                from memory_v2.scene_corrections import corrected_source_ids
                if pending.get("complete") and not corrected_source_ids(active_date, authority_path=authority_source.db_path):
                    projection = pending
    except Exception:
        projection = None
    if projection:
        compact = _build_compact_cross_day_memory_context(
            chronicle,
            active_date,
            projection,
            now=current,
            raw_budget=raw_budget,
            diary_cap=diary_cap,
            compact_budget=compact_budget,
            included_ids=included_ids,
        )
        if compact:
            return compact
    return _build_legacy_cross_day_memory_context(
        chronicle,
        active_date,
        now=current,
        raw_budget=raw_budget,
        diary_cap=diary_cap,
        included_ids=included_ids,
    )


__all__ = ["build_cross_day_memory_context"]
