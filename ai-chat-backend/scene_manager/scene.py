"""Scene segmentation and persistence.

Scenes are deliberately an index over ``conversation_messages`` rather than a
second copy of message data.  A user's messages are the only clock used for
the gap rule; assistant/wander/sentinel/reminder messages are attached to the
scene that preceded them.  ``divider`` messages close the current scene and
are retained as its final marker, so a divider never leaks into the next
conversation segment.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from message_roles import EVENT_ROLE, NOTIFICATION_ROLE

try:
    from time_utils import now_iso, parse_ts
except Exception:  # pragma: no cover - allows importing the package standalone
    from datetime import timezone

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def parse_ts(value: str) -> datetime:
        if not value:
            return datetime.min
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed
        except Exception:
            return datetime.min


logger = logging.getLogger(__name__)
DEFAULT_GAP_MINUTES = 15
DEFAULT_ICEBERG_MIN_AGE_DAYS = 3
DEFAULT_ICEBERG_LIMIT = 50


def _chronicle(chronicle=None):
    if chronicle is not None:
        return chronicle
    from event_chronicle import get_global_chronicle

    return get_global_chronicle()


def _lock_for(chronicle):
    return getattr(chronicle, "_lock", nullcontext())


def configured_gap_minutes(value: Optional[int] = None) -> int:
    """Return a safe positive gap, consulting settings when omitted."""
    if value is None:
        try:
            from settings_manager import get_setting

            value = get_setting("scene_gap_minutes")
        except Exception:
            value = DEFAULT_GAP_MINUTES
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = DEFAULT_GAP_MINUTES
    return max(1, min(value, 24 * 60))


def _message_dict(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message)
    if hasattr(message, "model_dump"):
        return dict(message.model_dump())
    if hasattr(message, "dict"):
        return dict(message.dict())
    return {
        key: getattr(message, key)
        for key in (
            "active_date",
            "calendar_date",
            "session_id",
            "timestamp",
            "role",
            "content",
            "reasoning_content",
            "message_id",
            "id",
            "is_wander",
            "is_sentinel",
            "is_reminder",
            "tool_summary",
            "tool_calls",
            "event_type",
            "images",
            "voice_attachment",
            "thinking_chain_json",
        )
        if hasattr(message, key)
    }


def _message_id(message: Dict[str, Any]) -> str:
    return str(message.get("message_id") or message.get("id") or "")


def _timestamp(message: Dict[str, Any]) -> str:
    return str(message.get("timestamp") or "")


def _sort_key(message: Dict[str, Any], index: int = 0):
    # parse_ts returns a timezone-aware minimum on malformed input.  The
    # original index keeps malformed/equal timestamps deterministic.
    return parse_ts(_timestamp(message)), index


def _is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "none", "null", "[]", "{}")
    return bool(value)


def _tool_call_count(message: Dict[str, Any]) -> int:
    raw = message.get("tool_calls")
    if raw in (None, "", [], {}):
        # Some old rows only retained a rendered tool summary.
        return 1 if _is_truthy(message.get("tool_summary")) else 0
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return 1
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        for key in ("calls", "tool_calls", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return len(value)
        return 1
    return 1


def _is_push(message: Dict[str, Any]) -> bool:
    return any(
        _is_truthy(message.get(key))
        for key in ("is_wander", "is_sentinel", "is_reminder")
    )


def _scene_id(active_date: str, session_id: str, start_ts: str) -> str:
    # Stable across rebuilds.  The first timestamp is a better identity than
    # the end timestamp because a later assistant message must not rename a
    # scene already referenced by a memory bucket.
    digest = hashlib.sha1(
        f"{active_date}\0{session_id}\0{start_ts}".encode("utf-8", "ignore")
    ).hexdigest()[:20]
    return f"scene_{digest}"


def _summary_for_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not messages:
        return {}
    messages = [_message_dict(m) for m in messages]
    messages.sort(key=lambda m: _sort_key(m))
    first = messages[0]
    last = messages[-1]
    active_date = str(first.get("active_date") or "")
    if not active_date:
        active_date = _timestamp(first)[:10]
    session_id = str(first.get("session_id") or "")
    users = [m for m in messages if str(m.get("role") or "") == "user"]
    return {
        "id": _scene_id(active_date, session_id, _timestamp(first)),
        "active_date": active_date,
        "session_id": session_id,
        "start_ts": _timestamp(first),
        "end_ts": _timestamp(last),
        "first_user_msg_id": _message_id(users[0]) if users else None,
        "last_user_msg_id": _message_id(users[-1]) if users else None,
        "msg_count": len(messages),
        "user_msg_count": len(users),
        "k_push_count": sum(1 for m in messages if _is_push(m)),
        "tool_call_count": sum(_tool_call_count(m) for m in messages),
        "recollection_count": 0,
        "retrieve_count": 0,
        "last_retrieved_at": None,
        "created_at": now_iso(),
    }


def _same_session(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return str(a.get("session_id") or "") == str(b.get("session_id") or "")


def segment_messages(messages: List[dict], gap_minutes: int = DEFAULT_GAP_MINUTES) -> List[dict]:
    """Split messages into scene skeletons.

    The returned dictionaries include a private ``_messages`` key so callers
    such as ``rebuild_day`` can persist the skeleton without re-querying.  It
    is intentionally removed from public DB/API responses.
    """
    gap_minutes = configured_gap_minutes(gap_minutes)
    prepared = [_message_dict(m) for m in (messages or [])]
    # Notifications are UI-only markers.  They are persisted in the message
    # ledger but never become scene material or a clock/boundary.
    prepared = [m for m in prepared if m.get("role") not in {NOTIFICATION_ROLE, EVENT_ROLE} and _timestamp(m)]
    prepared = [m for i, m in sorted(enumerate(prepared), key=lambda pair: _sort_key(pair[1], pair[0]))]
    if not prepared:
        return []

    scenes: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []
    last_user_ts = None

    def flush() -> None:
        nonlocal current, last_user_ts
        if current:
            summary = _summary_for_messages(current)
            summary["_messages"] = list(current)
            scenes.append(summary)
        current = []
        last_user_ts = None

    for message in prepared:
        role = str(message.get("role") or "")

        # A divider is a hard boundary but remains visible as the last marker
        # of the scene it closes.  This avoids adding artificial one-message
        # scenes for every topic-end divider.
        if role == "divider":
            if current:
                current.append(message)
                flush()
            else:
                current = [message]
                flush()
            continue

        if current and not _same_session(current[0], message):
            flush()

        if role == "user" and current and last_user_ts is not None:
            try:
                delta = (parse_ts(_timestamp(message)) - last_user_ts).total_seconds() / 60.0
            except Exception:
                delta = 0.0
            if delta > gap_minutes:
                flush()

        current.append(message)
        if role == "user":
            last_user_ts = parse_ts(_timestamp(message))

    flush()
    return scenes


def _public_scene(row: Any) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        row = dict(row)
    result = dict(row or {})
    result.pop("_messages", None)
    return result


def _rows_for_day(conn: sqlite3.Connection, active_date: str) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM conversation_scenes WHERE active_date=? ORDER BY start_ts, id",
        (active_date,),
    ).fetchall()
    return [_public_scene(r) for r in rows]


def _insert_scene(
    conn: sqlite3.Connection,
    scene: Dict[str, Any],
    recollection_count: int = 0,
    retrieve_count: int = 0,
    last_retrieved_at: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO conversation_scenes
           (id, active_date, session_id, start_ts, end_ts,
            first_user_msg_id, last_user_msg_id, msg_count, user_msg_count,
            k_push_count, tool_call_count, recollection_count,
            retrieve_count, last_retrieved_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            scene["id"],
            scene["active_date"],
            scene.get("session_id") or "",
            scene["start_ts"],
            scene["end_ts"],
            scene.get("first_user_msg_id"),
            scene.get("last_user_msg_id"),
            int(scene.get("msg_count", 0)),
            int(scene.get("user_msg_count", 0)),
            int(scene.get("k_push_count", 0)),
            int(scene.get("tool_call_count", 0)),
            int(recollection_count or 0),
            int(retrieve_count or 0),
            last_retrieved_at,
            scene.get("created_at") or now_iso(),
        ),
    )


def _overlap_seconds(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    try:
        start = max(parse_ts(a.get("start_ts", "")), parse_ts(b.get("start_ts", "")))
        end = min(parse_ts(a.get("end_ts", "")), parse_ts(b.get("end_ts", "")))
        return max(0.0, (end - start).total_seconds())
    except Exception:
        return 0.0


def rebuild_day(active_date: str, gap_minutes: Optional[int] = None, chronicle=None) -> List[dict]:
    """Recompute all scenes for one active date in one SQLite transaction.

    Existing recollection counters are carried over by stable scene id or by
    the best-overlapping old interval, so deleting/editing a message does not
    silently remove a user's explicit recollection marker.
    """
    active_date = str(active_date or "")[:10]
    if not active_date:
        return []
    gap_minutes = configured_gap_minutes(gap_minutes)
    chronicle = _chronicle(chronicle)
    messages = chronicle.get_messages_by_date(active_date)
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for message in messages:
        normalized = _message_dict(message)
        by_session.setdefault(str(normalized.get("session_id") or ""), []).append(normalized)

    segmented: List[Dict[str, Any]] = []
    for session_messages in by_session.values():
        segmented.extend(segment_messages(session_messages, gap_minutes=gap_minutes))
    segmented.sort(key=lambda scene: (parse_ts(scene.get("start_ts", "")), scene.get("id", "")))

    with _lock_for(chronicle):
        with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            old_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM conversation_scenes WHERE active_date=?", (active_date,)
            ).fetchall()]
            conn.execute("DELETE FROM conversation_scenes WHERE active_date=?", (active_date,))

            old_by_id = {r.get("id"): r for r in old_rows}
            used_old = set()
            persisted = []
            for scene in segmented:
                old = old_by_id.get(scene["id"])
                if old is None:
                    candidates = [
                        r for r in old_rows
                        if r.get("id") not in used_old
                        and str(r.get("session_id") or "") == str(scene.get("session_id") or "")
                        and _overlap_seconds(r, scene) > 0
                    ]
                    old = max(candidates, key=lambda r: _overlap_seconds(r, scene), default=None)
                if old is not None:
                    used_old.add(old.get("id"))
                recollection = int((old or {}).get("recollection_count") or 0)
                retrieve_count = int((old or {}).get("retrieve_count") or 0)
                last_retrieved_at = (old or {}).get("last_retrieved_at")
                if old and old.get("created_at"):
                    scene["created_at"] = old["created_at"]
                _insert_scene(
                    conn,
                    scene,
                    recollection_count=recollection,
                    retrieve_count=retrieve_count,
                    last_retrieved_at=last_retrieved_at,
                )
                persisted.append(_public_scene(scene | {
                    "recollection_count": recollection,
                    "retrieve_count": retrieve_count,
                    "last_retrieved_at": last_retrieved_at,
                }))
            conn.commit()
    try:
        from memory_v2.scene_digest_runtime import notify
        notify(active_date)
    except Exception:
        logger.warning("Scene rebuild digestion queue unavailable")
    return persisted


def _query_scene_messages(conn: sqlite3.Connection, scene: Dict[str, Any]) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT * FROM conversation_messages
           WHERE active_date=? AND session_id=? AND timestamp>=? AND timestamp<=?
           ORDER BY timestamp, id""",
        (
            scene.get("active_date", ""),
            scene.get("session_id") or "",
            scene.get("start_ts", ""),
            scene.get("end_ts", ""),
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def _last_scene(conn: sqlite3.Connection, active_date: str, session_id: str):
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT * FROM conversation_scenes
           WHERE active_date=? AND session_id=? ORDER BY start_ts DESC, id DESC LIMIT 1""",
        (active_date, session_id),
    ).fetchone()


def _scene_last_role(conn: sqlite3.Connection, scene: Dict[str, Any]) -> str:
    row = conn.execute(
        """SELECT role FROM conversation_messages
           WHERE active_date=? AND session_id=? AND timestamp>=? AND timestamp<=?
           ORDER BY timestamp DESC, id DESC LIMIT 1""",
        (
            scene.get("active_date", ""),
            scene.get("session_id") or "",
            scene.get("start_ts", ""),
            scene.get("end_ts", ""),
        ),
    ).fetchone()
    return str(row[0]) if row else ""


def _last_user_ts(conn: sqlite3.Connection, scene: Dict[str, Any]):
    row = conn.execute(
        """SELECT timestamp FROM conversation_messages
           WHERE active_date=? AND session_id=? AND timestamp>=? AND timestamp<=? AND role='user'
           ORDER BY timestamp DESC, id DESC LIMIT 1""",
        (
            scene.get("active_date", ""),
            scene.get("session_id") or "",
            scene.get("start_ts", ""),
            scene.get("end_ts", ""),
        ),
    ).fetchone()
    return parse_ts(row[0]) if row else None


def incremental_update(message: dict, gap_minutes: Optional[int] = None, chronicle=None) -> Optional[dict]:
    """Attach one newly inserted message to the appropriate scene.

    This function is intentionally synchronous: it runs at the end of the
    SQLite write path, after the message transaction has committed.  Out of
    order writes (imports/late edits) trigger a single-day rebuild rather than
    trying to mutate historical boundaries incrementally.
    """
    message = _message_dict(message)
    if message.get("role") in {NOTIFICATION_ROLE, EVENT_ROLE}:
        return None
    timestamp = _timestamp(message)
    if not timestamp:
        return None
    active_date = str(message.get("active_date") or timestamp[:10])[:10]
    session_id = str(message.get("session_id") or "")
    message["active_date"] = active_date
    message["session_id"] = session_id
    gap_minutes = configured_gap_minutes(gap_minutes)
    chronicle = _chronicle(chronicle)
    rebuild = False
    result = None

    with _lock_for(chronicle):
        with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            last_row = _last_scene(conn, active_date, session_id)
            last = dict(last_row) if last_row else None
            if last and parse_ts(timestamp) < parse_ts(last.get("end_ts", "")):
                rebuild = True
            elif message.get("role") == "user" and last:
                previous_user = _last_user_ts(conn, last)
                if previous_user is not None:
                    delta = (parse_ts(timestamp) - previous_user).total_seconds() / 60.0
                    if delta > gap_minutes:
                        last = None
            # A message following a divider must begin a fresh scene.
            if last and _scene_last_role(conn, last) == "divider":
                last = None

            if not rebuild:
                if last is None:
                    scene = _summary_for_messages([message])
                    _insert_scene(conn, scene)
                    result = _public_scene(scene)
                else:
                    updates = {
                        "end_ts": timestamp,
                        "msg_count": int(last.get("msg_count") or 0) + 1,
                        "user_msg_count": int(last.get("user_msg_count") or 0),
                        "k_push_count": int(last.get("k_push_count") or 0),
                        "tool_call_count": int(last.get("tool_call_count") or 0),
                        "first_user_msg_id": last.get("first_user_msg_id"),
                        "last_user_msg_id": last.get("last_user_msg_id"),
                    }
                    if message.get("role") == "user":
                        updates["user_msg_count"] += 1
                        user_id = _message_id(message) or None
                        if not updates["first_user_msg_id"]:
                            updates["first_user_msg_id"] = user_id
                        updates["last_user_msg_id"] = user_id
                    if _is_push(message):
                        updates["k_push_count"] += 1
                    updates["tool_call_count"] += _tool_call_count(message)
                    conn.execute(
                        """UPDATE conversation_scenes
                           SET end_ts=?, msg_count=?, user_msg_count=?, k_push_count=?,
                               tool_call_count=?, first_user_msg_id=?, last_user_msg_id=? WHERE id=?""",
                        (
                            updates["end_ts"], updates["msg_count"], updates["user_msg_count"],
                            updates["k_push_count"], updates["tool_call_count"],
                            updates["first_user_msg_id"], updates["last_user_msg_id"], last["id"],
                        ),
                    )
                    result = _public_scene({**last, **updates})
                conn.commit()

    if rebuild:
        rebuilt = rebuild_day(active_date, gap_minutes=gap_minutes, chronicle=chronicle)
        target = parse_ts(timestamp)
        for row in rebuilt:
            if str(row.get("session_id") or "") != session_id:
                continue
            if parse_ts(row.get("start_ts", "")) <= target <= parse_ts(row.get("end_ts", "")):
                return row
        return (rebuilt or [None])[-1]
    if message.get("role") in {"user", "divider"}:
        try:
            from memory_v2.scene_digest_runtime import notify
            notify(active_date)
        except Exception:
            logger.warning("Scene digestion queue unavailable; source index retained")
    return result


def _ephemeral_scenes_for_day(active_date: str, gap_minutes: int, chronicle) -> List[dict]:
    """Build a read-only preview when a caller requests another gap.

    The persisted index always follows the configured threshold.  API clients
    can still preview a different threshold without mutating that index.
    """
    messages = chronicle.get_messages_by_date(str(active_date or "")[:10])
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for message in messages:
        normalized = _message_dict(message)
        by_session.setdefault(str(normalized.get("session_id") or ""), []).append(normalized)
    scenes: List[Dict[str, Any]] = []
    for session_messages in by_session.values():
        scenes.extend(segment_messages(session_messages, gap_minutes=gap_minutes))
    scenes.sort(key=lambda scene: (parse_ts(scene.get("start_ts", "")), scene.get("id", "")))

    # Keep the only persisted scene state (recollection_count) visible in a
    # preview by carrying it over from the stored interval with the best
    # overlap.  All other anchor fields remain lazy and are recomputed by API.
    stored = get_scenes_for_day(active_date, chronicle=chronicle)
    for scene in scenes:
        old = next((row for row in stored if row.get("id") == scene.get("id")), None)
        if old is None:
            candidates = [
                row for row in stored
                if str(row.get("session_id") or "") == str(scene.get("session_id") or "")
                and _overlap_seconds(row, scene) > 0
            ]
            old = max(candidates, key=lambda row: _overlap_seconds(row, scene), default=None)
        scene["recollection_count"] = int((old or {}).get("recollection_count") or 0)
        scene["retrieve_count"] = int((old or {}).get("retrieve_count") or 0)
        scene["last_retrieved_at"] = (old or {}).get("last_retrieved_at")
    return [_public_scene(scene) for scene in scenes]


def get_scenes_for_day(
    active_date: str,
    chronicle=None,
    gap_minutes: Optional[int] = None,
) -> List[dict]:
    chronicle = _chronicle(chronicle)
    if gap_minutes is not None:
        requested = configured_gap_minutes(gap_minutes)
        configured = configured_gap_minutes()
        if requested != configured:
            return _ephemeral_scenes_for_day(active_date, requested, chronicle)
    with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
        return _rows_for_day(conn, str(active_date or "")[:10])


def get_scene_by_id(scene_id: str, chronicle=None) -> Optional[dict]:
    if not scene_id:
        return None
    chronicle = _chronicle(chronicle)
    with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM conversation_scenes WHERE id=?", (scene_id,)).fetchone()
        return _public_scene(row) if row else None


def get_scene_messages(scene: Dict[str, Any] | str, chronicle=None) -> List[dict]:
    chronicle = _chronicle(chronicle)
    if isinstance(scene, str):
        scene = get_scene_by_id(scene, chronicle=chronicle)
    if not scene:
        return []
    with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
        return _query_scene_messages(conn, scene)


def get_scene_for_ts(ts: str, active_date: str = "", session_id: str = "", chronicle=None) -> Optional[dict]:
    if not ts:
        return None
    chronicle = _chronicle(chronicle)
    with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        clauses = []
        params: List[Any] = []
        if active_date:
            clauses.append("active_date=?")
            params.append(str(active_date)[:10])
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        rows = conn.execute(
            "SELECT * FROM conversation_scenes" + (" WHERE " + " AND ".join(clauses) if clauses else ""),
            params,
        ).fetchall()
    target = parse_ts(ts)
    candidates = [dict(row) for row in rows if parse_ts(row["start_ts"]) <= target <= parse_ts(row["end_ts"])]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (parse_ts(row.get("start_ts", "")), row.get("id", "")), reverse=True)
    return _public_scene(candidates[0])


def get_scene_message_ids(scene: Dict[str, Any], chronicle=None) -> List[str]:
    return [
        str(m.get("message_id") or m.get("id"))
        for m in get_scene_messages(scene, chronicle=chronicle)
        if m.get("message_id") or m.get("id")
    ]


def add_recollection(scene_id: str, delta: int = 1, chronicle=None) -> Optional[dict]:
    """Atomically add/remove a persisted recollection marker.

    ``recollection_count`` is the one scene-side anchor state that is allowed
    to be persisted.  The API and the wander iceberg handler both use this
    function so that the update semantics (including the non-negative clamp)
    cannot drift between entry points.

    Args:
        scene_id: Stable ``conversation_scenes.id``.
        delta: Signed change.  Callers normally use ``+1``; negative values
            support the API's explicit remove action.
        chronicle: Optional EventChronicle instance, mainly for tests.

    Returns:
        The updated public scene skeleton, or ``None`` when the scene does not
        exist.
    """
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        return None
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        delta = 1
    chronicle = _chronicle(chronicle)

    with _lock_for(chronicle):
        with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id FROM conversation_scenes WHERE id=?", (scene_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE conversation_scenes
                   SET recollection_count=MAX(0, COALESCE(recollection_count, 0) + ?)
                   WHERE id=?""",
                (delta, scene_id),
            )
            updated = conn.execute(
                "SELECT * FROM conversation_scenes WHERE id=?", (scene_id,)
            ).fetchone()
            conn.commit()
    return _public_scene(updated) if updated else None


def touch_scene(
    scene_id: str,
    *,
    chronicle=None,
    retrieved_at: Optional[str] = None,
) -> Optional[dict]:
    """Record one successful retrieval of a scene.

    ``get_scene_*`` and the API list/detail paths are intentionally read-only;
    callers invoke this helper only after a scene actually contributed a
    retrieval result (memory bucket, anchor summary, or neighborhood clue).
    The counter is kept on the derived scene row solely as ranking metadata,
    while the scene's anchor state remains a lazy join.
    """
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        return None
    timestamp = str(retrieved_at or now_iso())
    chronicle = _chronicle(chronicle)
    try:
        with _lock_for(chronicle):
            with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id FROM conversation_scenes WHERE id=?", (scene_id,)
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    """UPDATE conversation_scenes
                       SET retrieve_count=COALESCE(retrieve_count, 0) + 1,
                           last_retrieved_at=?
                       WHERE id=?""",
                    (timestamp, scene_id),
                )
                updated = conn.execute(
                    "SELECT * FROM conversation_scenes WHERE id=?", (scene_id,)
                ).fetchone()
                conn.commit()
        return _public_scene(updated) if updated else None
    except sqlite3.Error as exc:
        # A stale/partially migrated maintenance DB must not break retrieval;
        # the next EventChronicle initialization will retry the additive ALTER.
        logger.debug("scene touch skipped for %s: %s", scene_id, exc)
        return None


def touch_scenes(
    scene_ids: Iterable[str],
    *,
    chronicle=None,
    retrieved_at: Optional[str] = None,
) -> List[dict]:
    """Batch :func:`touch_scene` for one retrieval pass.

    The helper is deliberately separate from read functions so a caller can
    make the mutation explicit and deduplicate multiple buckets from one
    scene.  Missing IDs are ignored and the returned rows are the updated
    skeletons in input order.
    """
    ids = list(dict.fromkeys(str(scene_id or "").strip() for scene_id in (scene_ids or []) if str(scene_id or "").strip()))
    if not ids:
        return []
    timestamp = str(retrieved_at or now_iso())
    chronicle = _chronicle(chronicle)
    placeholders = ",".join("?" for _ in ids)
    try:
        with _lock_for(chronicle):
            with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                existing = {
                    str(row[0])
                    for row in conn.execute(
                        f"SELECT id FROM conversation_scenes WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                }
                if not existing:
                    return []
                conn.execute(
                    f"""UPDATE conversation_scenes
                        SET retrieve_count=COALESCE(retrieve_count, 0) + 1,
                            last_retrieved_at=?
                        WHERE id IN ({placeholders})""",
                    [timestamp, *existing],
                )
                rows = {
                    str(row["id"]): _public_scene(row)
                    for row in conn.execute(
                        f"SELECT * FROM conversation_scenes WHERE id IN ({placeholders})",
                        list(existing),
                    ).fetchall()
                }
                conn.commit()
        return [rows[scene_id] for scene_id in ids if scene_id in rows]
    except sqlite3.Error as exc:
        logger.debug("batch scene touch skipped: %s", exc)
        return []


# Descriptive alias for callers that prefer to spell out the bookkeeping
# operation; both names intentionally share the same implementation.
touch_scene_retrieval = touch_scene


def _coerce_now(value: Any = None) -> datetime:
    """Return a timestamp compatible with :func:`parse_ts` results."""
    if value is None:
        value = now_iso()
    if isinstance(value, datetime):
        return value
    parsed = parse_ts(str(value))
    if parsed == datetime.min:
        return datetime.now()
    return parsed


def _scene_age_days(scene: Dict[str, Any], now: Any = None) -> float:
    """Calculate non-negative scene age from its closing timestamp."""
    end_ts = str(scene.get("end_ts") or "")
    if not end_ts:
        return -1.0
    end = parse_ts(end_ts)
    if end == datetime.min:
        return -1.0
    current = _coerce_now(now)
    try:
        # ``parse_ts`` preserves offsets; align a naive ``now`` when a test or
        # injected clock supplies one so subtraction never raises.
        if end.tzinfo is not None and current.tzinfo is None:
            current = current.replace(tzinfo=end.tzinfo)
        elif end.tzinfo is None and current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        return max(0.0, (current - end).total_seconds() / 86400.0)
    except (TypeError, ValueError, OverflowError):
        return -1.0


def _load_memory_buckets_sync() -> Optional[list[dict]]:
    """Read immutable old/V2 anchor evidence; unavailable is not an empty pool."""
    try:
        from memory_v2.scene_anchors import load_scene_anchors_sync
        return load_scene_anchors_sync()
    except Exception as exc:
        logger.debug("iceberg anchor read unavailable: %s", type(exc).__name__)
        return None


def get_iceberg_scenes(
    min_age_days: int = DEFAULT_ICEBERG_MIN_AGE_DAYS,
    limit: int = DEFAULT_ICEBERG_LIMIT,
    *,
    chronicle=None,
    memory_buckets: Optional[Iterable[dict]] = None,
    now: Any = None,
) -> List[dict]:
    """Return old scenes that currently have no lazy anchor.

    A scene is an iceberg when it has been closed for at least
    ``min_age_days`` and ``anchor_types_for`` reports no source-side anchor.
    The scene table remains a skeleton: ``anchor_types_for`` performs the
    joins at read time and no ``is_anchor`` flag is written here.
    """
    try:
        min_age = max(0.0, float(min_age_days))
    except (TypeError, ValueError):
        min_age = float(DEFAULT_ICEBERG_MIN_AGE_DAYS)
    try:
        max_count = max(0, int(limit))
    except (TypeError, ValueError):
        max_count = DEFAULT_ICEBERG_LIMIT
    if max_count == 0:
        return []

    chronicle_was_supplied = chronicle is not None
    chronicle = _chronicle(chronicle)
    if memory_buckets is None and not chronicle_was_supplied:
        memory_buckets = _load_memory_buckets_sync()
        if memory_buckets is None:
            # A global call must not classify scenes as icebergs while the
            # optional OB_Rev anchor source is unavailable.  Test/maintenance
            # callers with an explicit chronicle may intentionally omit the
            # external source and are handled as an empty local snapshot.
            return []
    elif memory_buckets is None:
        memory_buckets = []
    elif not isinstance(memory_buckets, list):
        memory_buckets = list(memory_buckets)

    with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM conversation_scenes ORDER BY end_ts ASC, start_ts ASC, id ASC"
        ).fetchall()

    # Imported lazily to avoid anchor.py -> scene.py import cycles.
    try:
        from .anchor import anchor_types_for
    except Exception:
        anchor_types_for = None
    if anchor_types_for is None:
        return []

    candidates: List[dict] = []
    for row in rows:
        scene = _public_scene(row)
        if _scene_age_days(scene, now=now) < min_age:
            continue
        try:
            anchor_types = anchor_types_for(
                scene,
                chronicle=chronicle,
                memory_buckets=memory_buckets,
            )
        except TypeError:
            # Lightweight adapters used by maintenance/import callers may
            # expose the original one-argument helper.  Preserve the lazy
            # join contract for the real implementation while remaining
            # compatible with that narrow interface.
            try:
                anchor_types = anchor_types_for(scene)
            except Exception as exc:
                logger.debug("iceberg anchor join skipped for %s: %s", scene.get("id"), exc)
                continue
        except Exception as exc:
            # We cannot prove the scene is unanchored after a failed join, so
            # fail closed rather than surfacing a possibly already-promoted
            # conversation as an iceberg.
            logger.debug("iceberg anchor join skipped for %s: %s", scene.get("id"), exc)
            continue
        if not anchor_types:
            candidates.append(scene)
            if len(candidates) >= max_count:
                break
    return candidates


def pick_iceberg_scene(
    min_age_days: int = DEFAULT_ICEBERG_MIN_AGE_DAYS,
    *,
    limit: int = DEFAULT_ICEBERG_LIMIT,
    chronicle=None,
    memory_buckets: Optional[Iterable[dict]] = None,
    now: Any = None,
    exclude_scene_ids: Optional[Iterable[str]] = None,
) -> Optional[dict]:
    """Pick one iceberg scene, weighting older scenes more heavily.

    The weighting intentionally mirrors ``MemoryFetchHandler``'s existing
    age rule: ``min(age_days, 30) + 1``.  It is a gentle preference rather than
    a hard oldest-first policy, so recent eligible scenes remain discoverable.
    """
    candidates = get_iceberg_scenes(
        min_age_days=min_age_days,
        limit=limit,
        chronicle=chronicle,
        memory_buckets=memory_buckets,
        now=now,
    )
    excluded = {str(scene_id) for scene_id in (exclude_scene_ids or []) if scene_id}
    if excluded:
        candidates = [scene for scene in candidates if str(scene.get("id") or "") not in excluded]
    if not candidates:
        return None
    weights = [
        min(max(_scene_age_days(scene, now=now), 0.0), 30.0) + 1.0
        for scene in candidates
    ]
    try:
        return random.choices(candidates, weights=weights, k=1)[0]
    except (TypeError, IndexError, ValueError):
        # Keep the helper useful with unusual injected random implementations
        # and preserve a deterministic fallback for a singleton pool.
        return random.choice(candidates)
