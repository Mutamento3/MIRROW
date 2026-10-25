"""One day-end interest lane over AI's verified game and music experiences."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

from . import autonomous, books, music
from .music_phases import experience_phases


def evidence_for_day(source_date: str, *, game_db_path: Path | None = None,
                     music_records: list[dict] | None = None,
                     event_lookup: Callable[[str], dict | None] | None = None) -> list[dict]:
    """Use detailed source records only here; ordinary context gets event impressions."""
    evidence: list[dict] = []
    path = game_db_path or books.ROOT / "events" / "wander_runtime.db"
    store = None
    if path.exists():
        from wander_manager.runtime_store import WanderRuntimeStore
        store = WanderRuntimeStore(path)
        if event_lookup is None and game_db_path is None:
            from event_chronicle import get_global_chronicle
            event_lookup = get_global_chronicle().get_message_by_msg_id
    event_cache: dict[str, dict | None] = {}

    def activity_event(activity_id: str) -> dict | None:
        if activity_id not in event_cache:
            event = event_lookup("activity_event_" + activity_id) if event_lookup else None
            # A failed local card commit still has its original active day in
            # the durable outbox; calendar fallback is only for legacy rows.
            if not event and store is not None:
                pending = store.get_activity_event_projection(activity_id)
                if pending:
                    event = {"active_date": pending["active_date"], "pending_projection": pending["status"] != "sent"}
            event_cache[activity_id] = event
        return event_cache[activity_id]

    for item in music.records() if music_records is None else music_records:
        item_date = str(item.get("created_at") or "")[:10]
        date_basis = "music_record_calendar_date"
        origin_activity_id = ""
        origin_run_id = ""
        execution = None
        music_identity = {}
        source_id = str(item.get("source_id") or "")
        if store is not None and source_id.startswith("wander-node:"):
            node = store.get_node(source_id.removeprefix("wander-node:"))
            if node:
                origin_activity_id = node["activity_id"]
                activity = store.get_activity(origin_activity_id)
                if not activity or activity.get("state") not in {"completed", "aborted", "interrupted"}:
                    continue
                origin_run_id = activity.get("run_id") or ""
                from wander_manager.music_receipt import music_execution
                execution = music_execution(node)
                payload = node.get("source_payload") or {}
                music_identity = {key: payload[key] for key in ("netease_song_id", "fingerprint") if payload.get(key)}
                event = activity_event(origin_activity_id)
                if event and event.get("active_date"):
                    item_date, date_basis = event["active_date"], "activity_event_active_date"
        if item_date != source_date or item.get("subject_id") != "k":
            continue
        mode = str(item.get("mode") or "")
        if mode not in {"analysis", "observed_playback", "manual_note", "listening"}:
            continue
        evidence.append({
            "id": "music-experience:" + str(item["id"]),
            "speaker": "k", "source_kind": "music_experience",
            "timestamp": item["created_at"],
            "active_date": item_date,
            "origin_activity_id": origin_activity_id,
            "origin_run_id": origin_run_id,
            "identity_basis": (
                "AI 的听歌体验" if mode == 'listening' else
                "AI 的歌曲分析；不代表实际播放" if mode == "analysis" else
                "经确认的播放体验" if mode == "observed_playback" else
                "登记为 AI 的手动音乐笔记；来源见原记录，不冒充亲身播放"
            ),
            "text": json.dumps({
                "title": item["title"], "artist": item["artist"], "mode": mode,
                **experience_phases(item),
                "source_id": item["source_id"],
                "activity_id": origin_activity_id, "date_basis": date_basis,
                "observation_scope": {"kind": "single_active_day", "active_date": item_date,
                                      "run_id": origin_run_id,
                                      "song_identity": music_identity},
                **({"music_execution": execution} if execution else {}),
            }, ensure_ascii=False),
        })

    if store is not None:
        from wander_manager.game_experience import chapter_facts
        from wander_manager.context_projection import execution_overview
        rows_by_activity: dict[str, list[dict]] = defaultdict(list)
        for row in store.list_game_history():
            rows_by_activity[str(row["activity_id"])].append(row)
        for activity_id, activity_rows in rows_by_activity.items():
            activity = store.get_activity(activity_id)
            if not activity or activity.get("state") not in {"completed", "aborted", "interrupted"}:
                continue
            event = activity_event(activity_id)
            if event and event.get("active_date"):
                if event["active_date"] != source_date:
                    continue
                day_rows = activity_rows
                date_basis = "activity_event_active_date"
            else:
                # Legacy activities predate durable event cards. Their exact
                # active day is unknown; preserve the explicit calendar basis.
                day_rows = [row for row in activity_rows if str(row.get("completed_at") or "")[:10] == source_date]
                date_basis = "legacy_node_calendar_date"
            if not day_rows:
                continue
            retained = store.get_settlement_impression(activity_id)
            first = day_rows[0].get("source_payload") or {}
            experience = {
                "game": first.get("game_name") or first.get("game") or "游戏",
                "playthrough_key": first.get("playthrough_key") or "",
                "activity_id": activity_id, "date_basis": date_basis,
                "confirmed_progress": execution_overview(activity, day_rows),
                "activity_settlement_impression": activity.get("summary") or "",
                "retained_impression": retained["impression"],
                "retained_source_node_ids": retained["source_node_ids"],
                "today_confirmed_steps": len(day_rows),
                "deep_source_node_ids": [row["node_id"] for row in day_rows],
                "chapters": chapter_facts(day_rows),
                "other_game_steps": [
                    {"node_id": row["node_id"], "source_description": row.get("source_summary") or "",
                     "reflection": row.get("reflection") or ""}
                    for row in day_rows if (row.get("source_payload") or {}).get("game") != "detroit"
                ],
            }
            evidence.append({
                "id": "game-activity:" + activity_id,
                "speaker": "k", "source_kind": "game_experience",
                "timestamp": max(str(row.get("completed_at") or "") for row in day_rows),
                "identity_basis": "AI 的真实游戏选择与回顾；作品剧情是虚构的",
                "text": json.dumps(experience, ensure_ascii=False),
            })
    return sorted(evidence, key=lambda item: (item["timestamp"], item["id"]))


async def run(source_date: str, session_id: str, llm, *, game_db_path: Path | None = None,
              music_records: list[dict] | None = None, evidence: list[dict] | None = None):
    if evidence is None:
        evidence = evidence_for_day(source_date, game_db_path=game_db_path,
                                    music_records=music_records)
    if not evidence:
        return {"status": "no_evidence", "items": []}
    return await autonomous.run_evidence(
        evidence, llm, source_date=source_date, session_id=session_id,
        origin="interest_daily", scope="interest",
    )


__all__ = ["evidence_for_day", "run"]
