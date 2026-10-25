"""One bounded textual view of a settled Wander timeline event."""

from __future__ import annotations

import json
from typing import Any


_DAILY_STATE_LABELS = {
    "completed": "完成", "settled": "已收束", "failed": "失败",
    "interrupted": "中断", "stopped": "停止", "canceled": "取消",
}
_DAILY_STATUS_LABELS = {
    "succeeded": "成功", "failed": "失败", "skipped": "跳过",
    "rejected": "拒绝", "blocked": "阻止", "unknown": "未知",
}


def _daily_stamp(timestamp: str) -> str:
    stamp = str(timestamp or "")
    if len(stamp) >= 16:
        return f"{stamp[:10]} {stamp[11:16]}"
    return stamp[:10]


def _daily_progress(overview: Any) -> str:
    """Surface progress only when the activity did not simply succeed."""
    if not isinstance(overview, dict):
        return ""
    parts = []
    state = str(overview.get("state") or "").strip()
    if state and state not in {"completed", "settled"}:
        parts.append("活动状态：" + _DAILY_STATE_LABELS.get(state, state))
    counts = overview.get("execution_status_counts")
    if isinstance(counts, dict):
        abnormal = {key: value for key, value in counts.items()
                    if value and str(key) != "succeeded"}
        if abnormal:
            parts.append("执行：" + "、".join(
                f"{_DAILY_STATUS_LABELS.get(str(key), str(key))} {value} 次"
                for key, value in abnormal.items()))
    return "；".join(parts)


def _same_text(left: Any, right: Any) -> bool:
    left = "".join(str(left or "").split())
    right = "".join(str(right or "").split())
    return bool(left and right and (left in right or right in left))


def _daily_material_lines(value: Any, timestamp: str, *, retained_impression='') -> list[str]:
    """References only; no material body is ever expanded or cut here."""
    if not isinstance(value, dict):
        return []
    stamp = _daily_stamp(timestamp)
    lines: list[str] = []
    for item in value.get("materials") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == 'xhs_interactions':
            from xhs_interactions.context import brief, capture
            view = capture(item)
            lines.append(brief(view, timestamp=timestamp,
                               include_feeling=view.get('reflection') != retained_impression))
        elif kind == "checkup" and item.get("version") == 1:
            conclusion = str(item.get("conclusion") or "uncertain")
            execution = str(item.get("execution_status") or "unknown")
            lines.append(f"查岗来源回执：{item.get('source_status') or conclusion}（执行={execution}；不代表目标已确认；明细在查岗记录）")
            from checkup_assessment import goal_assessment_text
            assessment = goal_assessment_text(item.get('goal_assessment'))
            if assessment:
                lines.append(assessment)
        elif kind == "game":
            from memory_v2.experience_material import project_material
            fact = project_material(item)
            if fact:
                lines.append(fact)
        elif kind == "music":
            title = str(item.get("title") or "").strip()
            artist = str(item.get("artist") or "").strip()
            lines.append("听歌：" + (f"《{title}》" + (f"（{artist}）" if artist else "")
                                     if title else "一首曲目") + "。")
        elif kind == "music_listening_attempt":
            title = str(item.get("title") or "").strip()
            receipt = item.get("music_execution") or {}
            lines.append("听歌尝试：" + (f"《{title}》" if title else "一首曲目")
                         + "；" + str(receipt.get("fact") or "完成状态未知") + "。")
        elif kind == "reading":
            count = str(item.get("displayed_message_count") or "").strip()
            lines.append("可回溯：回看历史对话摘录"
                         + (f" {count} 条" if count else "")
                         + "（原文在历史对话）")
        elif kind == "workshop":
            # Old material stores the whole room/work text in ``description``;
            # its title cannot be inferred reliably.  Give the verifiable
            # timestamp instead of guessing an artifact.
            lines.append("可回溯：工作坊材料（作品柜中可查；本次 "
                         + (stamp or "时间见事件") + "）")
        elif kind == "shopping":
            lines.append("可回溯：购物浏览材料（购物记录中可查；本次 "
                         + (stamp or "时间见事件") + "）")
        elif kind == "xhs":
            title = str(item.get("title") or "").strip()
            lines.append("可回溯：小红书帖子" + (f"《{title}》" if title else "")
                         + "（来源记录中可查）")
        elif kind == "xhs_publication":
            title = str(item.get("title") or "").strip()
            lines.append("可回溯：小红书发布" + (f"《{title}》" if title else "")
                         + "（发布记录保留在来源库）")
    return lines


def _daily_projection(summary: str, extra: Any, result: str,
                      timestamp: str = "") -> str:
    """Daily-entry view: event, progress, feeling and references."""
    lines = [result]
    progress = _daily_progress(extra.get("execution_overview"))
    if progress:
        lines.append(progress)
    thought = str(summary or "").strip()
    frozen = extra.get("memory_experience")
    interactions = (isinstance(frozen, dict) and any(
        item.get('kind') == 'xhs_interactions' for item in frozen.get('materials') or []))
    if thought and not interactions:
        lines.append("活动记录：" + thought)
    if isinstance(frozen, dict) and frozen.get("version") == 2:
        from memory_v2.experience_contract import impression_text
        retained = impression_text(frozen.get("retained_impression"),
                                   node_ids=frozen.get("retained_node_ids") or [])
        settlement = str(frozen.get("settlement_reflection") or "").strip()
        if retained:
            lines.append("AI 当时留下的感受：" + retained)
        if settlement and settlement != retained and not _same_text(settlement, thought):
            lines.append("AI 当时的活动回顾：" + settlement)
        lines.extend(_daily_material_lines(frozen, timestamp, retained_impression=retained))
    else:
        retained = extra.get("retained_experience")
        impression = str(retained.get("impression") or "").strip() if isinstance(retained, dict) else ""
        if impression:
            lines.append("AI 当时留下的感受：" + impression)
    return "\n".join(line for line in lines if line)


def format_wander_activity_event(summary: str, tool_calls: Any, message_id: str = "",
                                 *, level: str = "detail", timestamp: str = "") -> str:
    if level not in {"fact", "experience", "daily", "detail"}:
        raise ValueError("unknown wander event projection level")
    calls = tool_calls
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except (TypeError, ValueError):
            calls = []
    first = calls[0] if isinstance(calls, list) and calls else {}
    description = (
        str(first.get("description") or "").strip()[:60]
        if isinstance(first, dict) else ""
    ) or "一次漫想活动已结算"
    thought = str(summary or "").strip()
    game = isinstance(first, dict) and str(first.get("tool") or "").strip().endswith("玩游戏")
    source_label = "AI 的虚构游戏漫想事件" if game else "AI 的漫想事件"
    result = f"{source_label}：{description}。"
    extra = (first.get("extra_data") or {}) if isinstance(first, dict) else {}
    if not isinstance(extra, dict):
        extra = {}
    if level == "daily":
        return _daily_projection(summary, extra, result, timestamp)
    frozen = extra.get('memory_experience')
    if isinstance(frozen, dict) and frozen.get('version') == 2 and level != 'fact':
        from wander_manager.experience_presentation import frozen_text
        return frozen_text(frozen) or result
    overview = extra.get("execution_overview") if isinstance(extra, dict) else None
    if isinstance(overview, dict):
        result += "\n执行回执概况：" + json.dumps(overview, ensure_ascii=False)
    if level == "fact":
        return result
    retained = extra.get("retained_experience") if isinstance(extra, dict) else None
    impression = str(retained.get("impression") or "").strip() if isinstance(retained, dict) else ""
    if level == "experience":
        if impression:
            return result + f"\nK 当时选择留下的感受：{impression}"
        # Legacy non-game events had only a settlement thought. Preserve its
        # continuity; old game recaps are often whole chapter transcripts.
        if retained is None and not game and not str(first.get("tool") or "").strip().endswith("自己听歌") and thought:
            return result + f"\n当时的结算感受：{thought}"
        return result
    if game:
        from wander_manager.game_experience import (
            compact_chapter_facts, game_fallback_from_chapters,
            is_game_fallback_summary, read_activity_chapter_facts,
        )

        game_summary = (extra.get("game_summary") or {}) if isinstance(extra, dict) else {}
        game_summary = game_summary if isinstance(game_summary, dict) else {}
        chapters = game_summary.get("chapters") if isinstance(game_summary, dict) else None
        if not isinstance(chapters, list) and message_id.startswith("activity_event_"):
            chapters = read_activity_chapter_facts(message_id[len("activity_event_"):])
        if chapters:
            result += "\n已确认的游戏章节事实（虚构作品中的 AI 选择）：\n" + compact_chapter_facts(chapters)
        if is_game_fallback_summary(thought):
            thought = game_fallback_from_chapters(
                str(game_summary.get("game_name") or "游戏"), chapters or [],
                completed=game_summary.get("completed") is True,
            )
    if thought:
        result += ("\n" if game else "") + f"当时的结算感受：{thought}"
    if impression:
        result += f"\nK 当时选择留下的感受：{impression}"
    return result


def format_wander_communication(message: dict[str, Any]) -> str:
    """A sent utterance is evidence of communication, separate from execution."""
    return "历史通信记录（AI 当时发给人类伙伴的原话；行动状态由对应活动回执记录）：\n" + json.dumps({
        "message_id": message.get("_message_id") or "",
        "sent_at": message.get("_ts") or "",
        "speaker": "AI",
        "sent_wording": message.get("_spoken_content", message.get("content", "")),
    }, ensure_ascii=False)


__all__ = ["format_wander_activity_event", "format_wander_communication"]
