"""Player-visible, read-only chapter view over durable game nodes."""

from __future__ import annotations

from typing import Any

from .cedartoy_client import GAMES
from .game_handler import _moonlit_state


def _text(value: Any, limit: int = 8000) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _chapter(payload: dict[str, Any]) -> tuple[str, str]:
    game = payload.get("game")
    if game in {"mbti", "sins_virtues"}:
        return "test", "测试题目与结果"
    if game == "fishing":
        return "fishing", "钓鱼记录"
    for source in ("before", "outcome", "checkpoint"):
        state = payload.get(source) or {}
        if not isinstance(state, dict):
            continue
        chapter = state.get("chapter")
        if isinstance(chapter, dict):
            number = chapter.get("number")
            title = _text(chapter.get("title"), 120)
            protagonist = _text(chapter.get("protagonist"), 80)
            if number is not None or title:
                key = f"{number}:{title}"
                label = " · ".join(part for part in (
                    f"第 {number} 章" if number is not None else "",
                    title, protagonist,
                ) if part)
                return key, label
    if game == "moonlit":
        checkpoint = payload.get("checkpoint") or {}
        state = _moonlit_state(checkpoint) if isinstance(checkpoint, dict) else {}
        phase = _text(state.get("phase"), 80)
        if phase:
            return f"phase:{phase}", phase
    return "unknown", "游玩记录"


def _options(payload: dict[str, Any]) -> list[dict[str, str]]:
    before = payload.get("before") or {}
    if not isinstance(before, dict):
        return []
    choices = before.get("choices") or before.get("options")
    if not isinstance(choices, list) and payload.get("game") == "moonlit":
        choices = _moonlit_state(before).get("actions")
    if not isinstance(choices, list):
        return []
    result = []
    for item in choices[:80]:
        if isinstance(item, dict):
            label = _text(item.get("label") or item.get("name") or item.get("command"), 160)
            description = _text(item.get("text") or item.get("description"), 3000)
        elif isinstance(item, str):
            label, description = _text(item, 160), ""
        else:
            continue
        if label or description:
            result.append({"label": label, "text": description})
    return result


def _step(row: dict[str, Any], index: int) -> dict[str, Any]:
    payload = row.get("source_payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    before = payload.get("before") or {}
    after = payload.get("outcome") or {}
    params = payload.get("action_params") or {}
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    params = params if isinstance(params, dict) else {}
    action = _text(payload.get("action"), 80)
    choice = _text(params.get("label") or params.get("command"), 300)
    answers = params.get("a_scores") or params.get("answers")
    if isinstance(answers, list):
        choice = " · ".join(f"{number}: {answer}" for number, answer in enumerate(answers, 1))
    if not choice:
        choice = {"continue_scene": "继续阅读", "start_next_chapter": "进入下一章",
                  "save_chapter_reflection": "保存章节感想", "create_save": "创建新存档",
                  "new": "开启新局", "mbti_start": "开始16题人格测试",
                  "sins_virtues_start": "开始35题七宗罪七美德测试",
                  "mbti_get_result": "查看人格测试结果",
                  "sins_virtues_get_result": "查看七宗罪七美德结果"}.get(action, action)
    scene = _text(before.get("context") or before.get("text"))
    outcome = _text(after.get("context") or after.get("text"))
    ending = after.get("ending")
    if isinstance(ending, dict):
        ending = " · ".join(part for part in (
            _text(ending.get("title"), 200), _text(ending.get("narrative"), 4000)
        ) if part)
    if isinstance(ending, str) and ending and ending not in outcome:
        outcome = (outcome + "\n" + ending).strip()
    return {
        "node_id": str(row.get("node_id") or ""), "index": index,
        "at": str(row.get("completed_at") or row.get("started_at") or ""),
        "status": "completed" if row.get("execution_status") == "succeeded" else "unconfirmed",
        "action": action, "choice": choice, "reason": _text(payload.get("reason"), 1000),
        "scene": scene, "options": _options(payload),
        "outcome": outcome, "result_summary": _text(row.get("source_summary"), 1000),
        "reflection": _text(row.get("reflection"), 2000),
    }


def project_chronicle(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    matched = [row for row in rows if isinstance((row.get("source_payload") or {}), dict)
               and (row.get("source_payload") or {}).get("playthrough_key") == key]
    if not matched:
        return None
    first = matched[0]["source_payload"]
    if first.get("game") not in GAMES:
        return None
    chapters: list[dict[str, Any]] = []
    step_number = 0
    for row in rows:
        payload = row.get("source_payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("playthrough_key") not in {None, key}:
            continue
        if row.get("execution_status") == "succeeded" and payload.get("playthrough_key") != key:
            continue
        if row.get("execution_status") != "succeeded" and not chapters:
            continue
        chapter_key, label = _chapter(payload) if payload else (
            chapters[-1]["key"], chapters[-1]["title"])
        if not chapters or chapters[-1]["key"] != chapter_key:
            chapters.append({"key": chapter_key, "title": label, "steps": []})
        step_number += 1
        chapters[-1]["steps"].append(_step(row, step_number))
    return {"playthrough_key": key, "game": first["game"], "slot": first.get("slot"),
            "chapters": chapters}


def read_chronicle(creator: Any, key: str) -> dict[str, Any] | None:
    runner = creator._ensure_runtime_runner()
    runner.initialize()
    successes = [row for row in runner.store.list_game_history()
                 if isinstance(row.get("source_payload"), dict)
                 and row["source_payload"].get("playthrough_key") == key]
    if not successes:
        return None
    activity_ids = {row["activity_id"] for row in successes}
    rows = [row for activity_id in activity_ids for row in runner.store.list_nodes(activity_id)]
    rows.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("round_index") or 0)))
    return project_chronicle(rows, key)
