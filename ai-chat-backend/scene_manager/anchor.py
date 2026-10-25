"""Lazy scene-anchor joins.

``conversation_scenes`` intentionally stores no ``is_anchor`` column.  This
module derives the current anchor types from the authoritative source tables
and memory-bucket metadata whenever a scene is read.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .scene import get_scene_message_ids

logger = logging.getLogger(__name__)

# User-initiated anchors never leave the active pool.  Automatically derived
# anchors compete for the configurable capacity in ``get_active_anchors``.
STRONG_ANCHOR_TYPES = frozenset({
    "recollection",
    "bookmark",
    "ledger",
    "important_event",
})
WEAK_ANCHOR_TYPES = frozenset({
    "memory",
    "emotion_peak",
    "long_scene",
})
ANCHOR_TYPES = STRONG_ANCHOR_TYPES | WEAK_ANCHOR_TYPES
DEFAULT_ACTIVE_ANCHOR_LIMIT = 50
ACTIVE_ANCHOR_CACHE_TTL = 60.0  # 秒：活跃锚点池缓存（同 TODO 缓存模式，避免每轮扫 941 桶）
_active_anchor_cache: Dict[str, Any] = {"ts": 0.0, "results": None, "limit": None}


def _scene_bounds(scene: Dict[str, Any]):
    return str(scene.get("start_ts") or ""), str(scene.get("end_ts") or "")


def _has_event_in_range(conn: sqlite3.Connection, table: str, column: str, start: str, end: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column}>=? AND {column}<=? LIMIT 1",
            (start, end),
        ).fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


def _memory_bucket_anchor_types(
    scene_id: str, memory_buckets: Optional[Iterable[dict]]
) -> List[str]:
    """Return lazy anchor types supplied by matching bucket metadata.

    ``source_scene_id`` is the only link persisted by the Phase 1 bucket
    change.  Emotional-peak metadata already exists on some older buckets;
    exposing it here keeps that existing fact lazy rather than copying it
    into ``conversation_scenes``.
    """
    if not scene_id or not memory_buckets:
        return []
    result: List[str] = []
    for bucket in memory_buckets:
        if not isinstance(bucket, dict):
            continue
        meta = bucket.get("metadata") or {}
        source_scene_id = meta.get("source_scene_id") or bucket.get("source_scene_id")
        matched = scene_id in source_scene_id if isinstance(source_scene_id, (list, tuple, set)) else str(source_scene_id or "") == scene_id
        if not matched:
            continue
        result.append("memory")
        if meta.get("emotional_peak") or meta.get("emotion_peak") \
                or bucket.get("emotional_peak") or bucket.get("emotion_peak"):
            result.append("emotion_peak")
    return result


def _ledger_match(scene: Dict[str, Any]) -> bool:
    start, end = _scene_bounds(scene)
    if not start or not end:
        return False
    try:
        db_path = os.path.join(os.path.dirname(str(scene.get("_db_path") or "")), "ledger.db")
        if not os.path.dirname(db_path):
            return False
        if not os.path.exists(db_path):
            return False
        with sqlite3.connect(db_path, timeout=3) as conn:
            return _has_event_in_range(conn, "ledger_entries", "created", start, end)
    except Exception:
        return False


def anchor_types_for(
    scene: Dict[str, Any],
    *,
    chronicle=None,
    memory_buckets: Optional[Iterable[dict]] = None,
    message_ids: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return currently applicable anchor type keys for a scene.

    The function is synchronous so it can also be used by maintenance scripts.
    ``memory_buckets`` is optional because scanning Obsidian files is async in
    the normal OB_Rev client; the API supplies it when available.
    """
    if not scene:
        return []
    scene = dict(scene)
    types: List[str] = []
    if int(scene.get("recollection_count") or 0) > 0:
        types.append("recollection")

    try:
        long_threshold = 50
        from settings_manager import get_setting

        configured = get_setting("scene_long_message_threshold")
        if configured is not None:
            long_threshold = max(1, int(configured))
    except Exception:
        pass
    if int(scene.get("msg_count") or 0) >= long_threshold:
        types.append("long_scene")

    chronicle = chronicle
    if chronicle is None:
        try:
            from event_chronicle import get_global_chronicle

            chronicle = get_global_chronicle()
        except Exception:
            chronicle = None

    start, end = _scene_bounds(scene)
    if chronicle is not None and start and end:
        scene["_db_path"] = chronicle.db_path
        ids = [str(i) for i in (message_ids or []) if i]
        if not ids:
            try:
                ids = get_scene_message_ids(scene, chronicle=chronicle)
            except Exception:
                ids = []
        try:
            with sqlite3.connect(chronicle.db_path, timeout=3) as conn:
                bookmark_hit = _has_event_in_range(conn, "bookmarks", "original_timestamp", start, end)
                if not bookmark_hit and ids:
                    marks = ",".join("?" for _ in ids)
                    try:
                        bookmark_hit = bool(conn.execute(
                            f"SELECT 1 FROM bookmarks WHERE original_msg_id IN ({marks}) LIMIT 1", ids
                        ).fetchone())
                    except sqlite3.Error:
                        bookmark_hit = False
                if bookmark_hit:
                    types.append("bookmark")

                # Important events are date-addressed rather than timestamped;
                # full-day events are exact, month/year events are useful lazy
                # context too and therefore count as an event anchor.
                active_date = str(scene.get("active_date") or "")[:10]
                if active_date:
                    try:
                        event_hit = bool(conn.execute(
                            """SELECT 1 FROM important_events
                               WHERE (date_precision='full' AND date_string=?)
                                  OR (date_precision='year_month' AND date_string LIKE ?)
                                  OR (date_precision='year_only' AND date_string LIKE ?)
                               LIMIT 1""",
                            (active_date, active_date[:7] + "%", active_date[:4] + "%"),
                        ).fetchone())
                    except sqlite3.Error:
                        event_hit = False
                    if event_hit:
                        types.append("important_event")

        except Exception as exc:
            logger.debug("scene anchor join skipped: %s", exc)

        if _ledger_match(scene):
            types.append("ledger")

    bucket_types = _memory_bucket_anchor_types(str(scene.get("id") or ""), memory_buckets)
    if bucket_types:
        types.extend(bucket_types)

    # Stable order makes API output deterministic and easier to inspect.
    order = {
        "recollection": 0,
        "memory": 1,
        "bookmark": 2,
        "ledger": 3,
        "important_event": 4,
        "emotion_peak": 5,
        "long_scene": 6,
    }
    return list(dict.fromkeys(sorted(types, key=lambda value: order.get(value, 99))))


def build_anchor_meta(scene: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """Return the API-facing lazy anchor object."""
    types = anchor_types_for(scene, **kwargs)
    return {"is_anchor": bool(types), "types": types}


def anchor_strength(anchor_types: Iterable[str]) -> str:
    """Classify a lazy anchor type list without persisting the classification."""
    types = {str(value) for value in (anchor_types or [])}
    if types & STRONG_ANCHOR_TYPES:
        return "strong"
    if types & WEAK_ANCHOR_TYPES:
        return "weak"
    return "none"


def _configured_active_anchor_limit(value: Optional[int] = None) -> int:
    if value is None:
        try:
            from settings_manager import get_setting

            value = get_setting("active_anchor_limit")
        except Exception:
            value = DEFAULT_ACTIVE_ANCHOR_LIMIT
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_ACTIVE_ANCHOR_LIMIT


def get_active_anchors(
    limit: Optional[int] = None,
    *,
    N: Optional[int] = None,
    chronicle=None,
    memory_buckets: Optional[Iterable[dict]] = None,
) -> List[dict]:
    """Return the current active anchor pool for daily semantic retrieval.

    Anchor types are recomputed from their authoritative source tables on each
    call.  Strong anchors are always retained; weak anchors fill the remaining
    slots (ordered by retrieval frequency, then recency).  Excluded weak
    scenes are never deleted and can re-enter after a later explicit
    :func:`scene_manager.scene.touch_scene`.
    """
    # ``N`` mirrors the design document terminology while ``limit`` keeps the
    # Python API idiomatic; accepting both is harmless and helps maintenance
    # callers/readability.
    if N is not None:
        limit = N
    max_count = _configured_active_anchor_limit(limit)
    # 默认全局调用（未显式传 bucket 快照 / chronicle 实例）走 TTL 缓存，避免每轮
    # 对话重复扫描全部记忆桶（~941 文件）。显式传参的调用绕过缓存（测试/维护）。
    use_cache = memory_buckets is None and chronicle is None
    if use_cache:
        now = time.monotonic()
        cached = _active_anchor_cache
        if (
            cached["results"] is not None
            and cached["limit"] == max_count
            and (now - cached["ts"]) < ACTIVE_ANCHOR_CACHE_TTL
        ):
            return [dict(item) for item in cached["results"]]
    if chronicle is None:
        try:
            from event_chronicle import get_global_chronicle

            chronicle = get_global_chronicle()
        except Exception:
            return []

    # The async API normally passes one bucket snapshot.  Synchronous callers
    # (maintenance/tests) get the same best-effort snapshot used by iceberg
    # selection so memory-linked scenes are not silently misclassified.
    if memory_buckets is None:
        try:
            from .scene import _load_memory_buckets_sync

            memory_buckets = _load_memory_buckets_sync()
        except Exception:
            memory_buckets = []
    if memory_buckets is None:
        memory_buckets = []
    if not isinstance(memory_buckets, list):
        memory_buckets = list(memory_buckets)

    try:
        with sqlite3.connect(chronicle.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM conversation_scenes ORDER BY start_ts, id"
            ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("active anchor pool unavailable: %s", exc)
        return []

    strong: List[dict] = []
    weak: List[dict] = []
    for row in rows:
        scene = dict(row)
        try:
            types = anchor_types_for(
                scene,
                chronicle=chronicle,
                memory_buckets=memory_buckets,
            )
        except Exception as exc:
            # A failed lazy join cannot prove this scene is active.  Fail
            # closed so an unavailable source does not flood the pool.
            logger.debug("active anchor join skipped for %s: %s", scene.get("id"), exc)
            continue
        strength = anchor_strength(types)
        if strength == "none":
            continue
        scene["anchor_types"] = types
        scene["is_anchor"] = True
        scene["anchor_strength"] = strength
        if strength == "strong":
            strong.append(scene)
        else:
            weak.append(scene)

    # Stable deterministic order for strong anchors.  Their inclusion is not
    # affected by frequency or the capacity setting.
    strong.sort(key=lambda scene: (str(scene.get("start_ts") or ""), str(scene.get("id") or "")))

    def _weak_sort_key(scene: dict):
        try:
            count = int(scene.get("retrieve_count") or 0)
        except (TypeError, ValueError):
            count = 0
        return (
            count,
            str(scene.get("last_retrieved_at") or ""),
            str(scene.get("start_ts") or ""),
            str(scene.get("id") or ""),
        )

    weak.sort(key=_weak_sort_key, reverse=True)
    # N is the total active-pool target; strong anchors may exceed it by
    # design, while weak anchors only fill the remaining capacity.
    weak_slots = max(0, max_count - len(strong))
    result = strong + weak[:weak_slots]
    if use_cache:
        _active_anchor_cache.update(
            {"ts": time.monotonic(), "results": result, "limit": max_count}
        )
    return result
