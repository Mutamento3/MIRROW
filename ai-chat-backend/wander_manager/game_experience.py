"""Source-backed chapter facts derived from confirmed, visible game moves."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _text(value: Any, limit: int = 3000) -> str:
    return value[:limit].strip() if isinstance(value, str) else ""


def chapter_facts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep choice paths and AI's saved reflections, never hidden save state."""
    chapters: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("execution_status") != "succeeded":
            continue
        payload = row.get("source_payload") or {}
        if not isinstance(payload, dict) or payload.get("game") != "detroit":
            continue
        before = payload.get("before") or {}
        if not isinstance(before, dict):
            continue
        action = payload.get("action")
        outcome = payload.get("outcome") or {}
        chapter = (outcome.get("chapter") if action == "start_next_chapter" and isinstance(outcome, dict)
                   else before.get("chapter")) or before.get("chapter") or {}
        if not isinstance(chapter, dict):
            continue
        number, title = chapter.get("number"), _text(chapter.get("title"), 120)
        if number is None and not title:
            continue
        playthrough_key = _text(payload.get("playthrough_key"), 160)
        key = (playthrough_key, f"{number}:{title}")
        fact = by_key.get(key)
        if fact is None:
            fact = {"playthrough_key": playthrough_key, "number": number, "title": title,
                    "protagonist": _text(chapter.get("protagonist"), 80),
                    "choices": [], "ending": "", "reflection": "", "source_node_ids": []}
            by_key[key] = fact
            chapters.append(fact)
        node_id = str(row.get("node_id") or "")
        if node_id:
            fact["source_node_ids"].append(node_id)
        params = payload.get("action_params") or {}
        params = params if isinstance(params, dict) else {}
        if action == "play_step":
            label = _text(params.get("label"), 160)
            options = before.get("choices") or before.get("options") or []
            chosen = next((item for item in options if isinstance(item, dict)
                           and item.get("label") == label), {}) if isinstance(options, list) else {}
            fact["choices"].append({"node_id": node_id, "label": label,
                                    "text": _text(chosen.get("text"), 500),
                                    "reason": _text(payload.get("reason"), 500)})
        if action == "save_chapter_reflection":
            fact["reflection"] = _text(params.get("reflection"))
        ending = ((outcome.get("ending") if isinstance(outcome, dict) else None)
                  if action == "start_next_chapter" else
                  before.get("ending") or (outcome.get("ending") if isinstance(outcome, dict) else None))
        if isinstance(ending, dict):
            fact["ending"] = _text(ending.get("title"), 160)
        elif isinstance(ending, str):
            fact["ending"] = _text(ending, 160)
    return chapters


def compact_chapter_facts(chapters: list[dict[str, Any]]) -> str:
    """Bounded reading projection; full steps remain in the chapter book."""
    lines = []
    for chapter in chapters:
        heading = f"第{chapter.get('number')}章《{chapter.get('title') or '未命名'}》"
        choices = chapter.get("choices") or []
        path = " → ".join(
            f"{_text(choice.get('label'), 30)}:{_text(choice.get('text'), 35)}"
            f"（{_text(choice.get('reason'), 45)}）"
            for choice in choices if isinstance(choice, dict)
        )
        parts = [heading]
        if path:
            parts.append(f"已选路径：{path[:360]}")
        if chapter.get("ending"):
            parts.append(f"结局：{_text(chapter['ending'], 100)}")
        if chapter.get("reflection"):
            parts.append(f"AI 的章末感想：{_text(chapter['reflection'], 350)}")
        if not chapter.get("reflection"):
            parts.append("本章尚无已保存的章末感想")
        lines.append("；".join(parts))
    return "\n".join(lines)


def game_step_summary(receipt: dict[str, Any]) -> str:
    """A readable execution line; the full CedarToy receipt stays in the node."""
    game = str(receipt.get("game_name") or "游戏")
    action = str(receipt.get("action") or "")
    before = receipt.get("before") or {}
    outcome = receipt.get("outcome") or {}
    before = before if isinstance(before, dict) else {}
    outcome = outcome if isinstance(outcome, dict) else {}
    scene = outcome if action == "start_next_chapter" else before
    chapter = scene.get("chapter") or {}
    chapter = chapter if isinstance(chapter, dict) else {}
    number, title = chapter.get("number"), _text(chapter.get("title"), 120)
    place = (f"第{number}章" if number is not None else "") + (f"《{title}》" if title else "")
    place = f"{game}{place}" if place else game
    params = receipt.get("action_params") or {}
    params = params if isinstance(params, dict) else {}
    if action == "play_step":
        label = _text(params.get("label"), 80)
        event = f"选择了 {label}" if label else "作出了一次选择"
    else:
        event = {
            "continue_scene": "继续读了当前场景",
            "save_chapter_reflection": "保存了章末感想",
            "start_next_chapter": "进入了新章节",
            "create_save": "建立了新存档",
            "new": "开启了新周目",
            "cmd": "推进了一步",
            "mbti_start": "开始了16题人格测试",
            "sins_virtues_start": "开始了35题七宗罪七美德测试",
            "mbti_answer_batch": "提交了16题作答",
            "sins_virtues_answer_batch": "提交了35题作答",
            "mbti_get_result": "查看了已完成的测试结果",
            "sins_virtues_get_result": "查看了已完成的测试结果",
        }.get(action, "完成了一步游戏行动")
    outcome_text = _text(outcome.get("text"), 180).split("[STATE]", 1)[0].strip()
    if action == "cmd" and outcome_text and not outcome_text.startswith(("{", "[")):
        event += f"，结果是{outcome_text}"
    suffix = ("，测试结果已确认" if receipt.get("game") in {"mbti", "sins_virtues"}
              else "，本周目已确认完结") if receipt.get("game_complete") is True else ""
    return f"{place}：{event}{suffix}。"


def is_game_fallback_summary(summary: str) -> bool:
    """Recognize the exact legacy fallback prefix, not ordinary AI prose."""
    return str(summary or "").startswith("刚才在🎮 玩游戏，完成了：")


def game_fallback_from_chapters(name: str, chapters: list[dict[str, Any]],
                                *, completed: bool = False) -> str:
    """One factual reading view for old cards and new fallback settlements."""
    last = chapters[-1] if chapters else None
    place = (f"推进到第{last['number']}章" if last and last.get("number") is not None else "推进了一段")
    if last and last.get("title"):
        place += f"《{last['title']}》"
    choices = sum(len(chapter.get("choices") or []) for chapter in chapters)
    action = f"，作出了{choices}次选择" if choices else ""
    ending = "，本周目已确认完结" if completed else ""
    return f"{name}{place}{action}{ending}。整体感想暂未形成；实际选择和当时留下的想法可在游戏记录中查看。"


def game_fallback_summary(activity: dict[str, Any], nodes: list[dict[str, Any]]) -> str:
    """Truthful compact record when the settlement model was unavailable."""
    confirmed = [node for node in nodes if node.get("execution_status") == "succeeded"
                 and isinstance(node.get("source_payload"), dict)
                 and node["source_payload"].get("game")]
    if not confirmed:
        return "这次游戏没有确认完成的步骤，整体感想暂未形成。"
    latest = confirmed[-1]["source_payload"]
    name = str(latest.get("game_name") or "游戏")
    chapters = chapter_facts(confirmed)
    completed = any(node["source_payload"].get("game_complete") is True for node in confirmed)
    return game_fallback_from_chapters(name, chapters, completed=completed)


def read_activity_chapter_facts(activity_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Read old settled events without rewriting conversation or runtime data."""
    if len(activity_id) != 32 or any(c not in "0123456789abcdef" for c in activity_id.lower()):
        return []
    path = db_path or Path(__file__).resolve().parents[1] / "events" / "wander_runtime.db"
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as conn:
            conn.execute("PRAGMA query_only=ON")
            raw = conn.execute(
                "SELECT node_id, execution_status, source_payload FROM wander_nodes "
                "WHERE activity_id=? ORDER BY round_index", (activity_id,)
            ).fetchall()
    except sqlite3.Error:
        return []
    rows = []
    for node_id, status, payload_text in raw:
        try:
            payload = json.loads(payload_text or "{}")
        except (TypeError, ValueError):
            continue
        rows.append({"node_id": node_id, "execution_status": status, "source_payload": payload})
    return chapter_facts(rows)


__all__ = ["chapter_facts", "compact_chapter_facts", "game_step_summary",
           "is_game_fallback_summary", "game_fallback_from_chapters",
           "game_fallback_summary", "read_activity_chapter_facts"]
