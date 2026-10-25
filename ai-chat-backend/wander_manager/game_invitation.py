"""Human UI invitation and read-only game-life projection for AI's Wander games."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .cedartoy_client import CedarToyClient, CedarToyError, GAMES, configured
from .game_handler import _visible
from . import game_chat_gate

logger = logging.getLogger(__name__)
_drivers: dict[str, asyncio.Task] = {}
_driver_wakes: dict[str, asyncio.Event] = {}
EVENT_TYPE = "ui_action:play_game"


def invitation_fact(content: str = "") -> str:
    fact = "人类伙伴点击了机生活的游戏快捷邀请，邀请 AI 自己玩游戏；"
    if content:
        fact += f"界面固定文案为：{str(content).strip()}；"
    return fact + "这是界面生成的操作，不是人类伙伴手写的私聊，也不是人类伙伴本人游玩的记录。"


async def _drive(runner: Any, run_id: str, wake: asyncio.Event) -> None:
    try:
        while True:
            result = await runner.tick(only_run_id=run_id)
            run = runner.store.get_run(run_id)
            if result.action == "invitation_finished" or not run or run.get("state") in {"completed", "interrupted", "aborted"}:
                return
            try:
                await asyncio.wait_for(wake.wait(), timeout=min(max(result.delay_seconds, 1.0), 30.0))
            except asyncio.TimeoutError:
                pass
            wake.clear()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("游戏快捷邀请运行中断: run_id=%s", run_id)
    finally:
        run = runner.store.get_run(run_id)
        if not run or run.get("state") in {"completed", "interrupted", "aborted"}:
            game_chat_gate.release()


def _start_driver(runner: Any, run_id: str) -> None:
    task = _drivers.get(run_id)
    if task and not task.done():
        return
    wake = asyncio.Event()
    _driver_wakes[run_id] = wake
    task = asyncio.create_task(_drive(runner, run_id, wake))
    _drivers[run_id] = task
    def finished(_: asyncio.Task) -> None:
        _drivers.pop(run_id, None)
        _driver_wakes.pop(run_id, None)
    task.add_done_callback(finished)


async def stop_drivers() -> None:
    tasks = list(_drivers.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def invite(creator: Any, game: str) -> dict[str, str]:
    if game not in GAMES:
        raise ValueError("unsupported_game")
    runner = creator._ensure_runtime_runner()

    async def persist() -> dict[str, Any]:
        from ui_action_messages import invite_action
        return await invite_action(f"AI，玩一会儿《{GAMES[game]}》吧～",
                                   EVENT_TYPE, "play_game")

    game_chat_gate.reserve()
    try:
        await game_chat_gate.drain()
        receipt = await runner.invite_game(game, persist)
    except BaseException:
        game_chat_gate.release()
        raise
    _start_driver(runner, receipt["run_id"])
    return {"status": "preparing", "game": game, **receipt}


def resume_pending(creator: Any) -> None:
    """Restore a user-invited game when the backend restarted during its run."""
    runner = creator._ensure_runtime_runner()
    runner.initialize()
    active = runner.store.get_active_run()
    if active and active.get("trigger_reason") == "ui_action:play_game":
        if not game_chat_gate.status()["busy"]:
            game_chat_gate.reserve()
        _start_driver(runner, str(active["run_id"]))


def stop_invited_game(creator: Any) -> dict[str, str]:
    runner = creator._ensure_runtime_runner()
    runner.initialize()
    active = runner.store.get_active_run()
    if not active or active.get("trigger_reason") != EVENT_TYPE:
        raise RuntimeError("no_invited_game")
    for activity in runner.store.list_activities(active["run_id"]):
        if activity["state"] not in {"completed", "interrupted", "aborted"}:
            if activity["state"] != "settling":
                runner.controller.request_game_stop(activity["activity_id"])
            break
    _start_driver(runner, str(active["run_id"]))
    runner.wake()
    wake = _driver_wakes.get(str(active["run_id"]))
    if wake:
        wake.set()
    return {"status": "stopping", "run_id": str(active["run_id"])}


def _save_entries(value: Any, game: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        identified = str(value.get("game") or value.get("game_id") or game)
        slot = value.get("slot")
        if identified in {"mbti", "sins_virtues", "fishing"} and slot is None and any(
                key in value for key in ("latest_result", "completed_at", "points", "encyclopedia")):
            slot = 1
        if identified in GAMES and isinstance(slot, int) and 1 <= slot <= 5:
            entry = {"game": identified, "slot": slot,
                           "name": str(value.get("name") or value.get("title") or "")[:100],
                           "status": str(value.get("status") or value.get("state") or "")[:80],
                           "chapter": str(value.get("chapter") or "")[:80],
                           "updated_at": str(value.get("updated_at") or value.get("completed_at") or "")[:50]}
            if identified in {"mbti", "sins_virtues", "fishing"}:
                stats = []
                for field, label in (("latest_result", "最近结果"), ("points", "点数"), ("encyclopedia", "图鉴")):
                    item = value.get(field)
                    if isinstance(item, (str, int, float)):
                        stats.append(f"{label}：{str(item)[:100]}")
                    elif field == "latest_result" and item is not None:
                        stats.append("已保存测试结果")
                entry["summary"] = " · ".join(stats)
            result.append(entry)
        for key, child in value.items():
            result.extend(_save_entries(child, key if key in GAMES else identified if identified in GAMES else ""))
    elif isinstance(value, list):
        for child in value:
            result.extend(_save_entries(child, game))
    return result


def _history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("source_payload") or {}
        if not isinstance(payload, dict) or payload.get("game") not in GAMES:
            continue
        key = str(payload.get("playthrough_key") or "")
        if not key:
            continue
        group = groups.setdefault(key, {"playthrough_key": key, "game": payload["game"],
                                    "slot": payload.get("slot"), "steps": 0,
                                    "completed": False, "last_played_at": "", "summary": "",
                                    "last_action": "", "checkpoint": {}})
        group["steps"] += 1
        group["completed"] = group["completed"] or payload.get("game_complete") is True
        group["last_played_at"] = str(row.get("completed_at") or group["last_played_at"] or "")
        group["last_action"] = str(row.get("source_summary") or "")[:240]
        if row.get("activity_summary"):
            group["summary"] = str(row["activity_summary"])[:1200]
        checkpoint = payload.get("checkpoint") or {}
        if isinstance(checkpoint, dict):
            group["checkpoint"] = {key: _visible(checkpoint[key]) for key in
                                   ("status", "phase", "chapter", "chapter_id", "node_id") if key in checkpoint}
    return sorted(groups.values(), key=lambda item: item["last_played_at"], reverse=True)


async def dashboard(creator: Any) -> dict[str, Any]:
    runner = creator._ensure_runtime_runner()
    runner.initialize()
    active = runner.store.get_active_run()
    invitation = ((active.get("context_snapshot") or {}).get("game_invitation") or {}) if active else {}
    active_activity = next((item for item in runner.store.list_activities(active["run_id"])
                            if item.get("state") not in {"completed", "aborted", "interrupted", "planned"}), None) if active else None
    active_nodes = runner.store.list_nodes(active_activity["activity_id"]) if active_activity else []
    active_game = invitation.get("game") or next((payload.get("game") for node in reversed(active_nodes)
        if isinstance((payload := node.get("source_payload")), dict) and payload.get("game") in GAMES), "")
    data: dict[str, Any] = {
        "configured": configured(), "games": [{"id": game, "name": name} for game, name in GAMES.items()],
        "saves": [], "playthroughs": _history(runner.store.list_game_history()),
        "active": {"run_id": active["run_id"], "game": active_game, "state": active["state"],
                   "activity_type": active_activity.get("activity_type") if active_activity else "planning",
                   "invited": active.get("trigger_reason") == EVENT_TYPE}
                  if active else None,
        "remote_status": "unconfigured" if not configured() else "unavailable",
    }
    if not configured():
        return data
    try:
        client = CedarToyClient()
        account, detroit = await asyncio.gather(client.my_saves(), client.play("detroit", "list_saves"))
        entries = _save_entries(account.get("saves") if isinstance(account, dict) else {})
        entries.extend(_save_entries(detroit.get("saves") if isinstance(detroit, dict) else [], "detroit"))
        data["saves"] = list({(item["game"], item["slot"]): item for item in entries}.values())
        data["remote_status"] = "ok"
    except CedarToyError:
        pass
    return data
