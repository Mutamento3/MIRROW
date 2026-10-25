"""Scene segmentation and memory/source anchoring helpers.

The package deliberately keeps scene rows small.  Anchor state is derived at
read time by :mod:`scene_manager.anchor`; only the user-selected recollection
counter lives in ``conversation_scenes``.
"""

from .scene import (
    DEFAULT_GAP_MINUTES,
    DEFAULT_ICEBERG_LIMIT,
    DEFAULT_ICEBERG_MIN_AGE_DAYS,
    add_recollection,
    touch_scene,
    touch_scenes,
    touch_scene_retrieval,
    get_scene_for_ts,
    get_scene_messages,
    get_scene_by_id,
    get_scenes_for_day,
    get_iceberg_scenes,
    incremental_update,
    pick_iceberg_scene,
    rebuild_day,
    segment_messages,
)
from .anchor import (
    ANCHOR_TYPES,
    STRONG_ANCHOR_TYPES,
    WEAK_ANCHOR_TYPES,
    anchor_strength,
    anchor_types_for,
    build_anchor_meta,
    get_active_anchors,
)

__all__ = [
    "DEFAULT_GAP_MINUTES",
    "DEFAULT_ICEBERG_MIN_AGE_DAYS",
    "DEFAULT_ICEBERG_LIMIT",
    "segment_messages",
    "rebuild_day",
    "incremental_update",
    "add_recollection",
    "touch_scene",
    "touch_scenes",
    "touch_scene_retrieval",
    "get_iceberg_scenes",
    "pick_iceberg_scene",
    "get_scene_for_ts",
    "get_scenes_for_day",
    "get_scene_by_id",
    "get_scene_messages",
    "ANCHOR_TYPES",
    "STRONG_ANCHOR_TYPES",
    "WEAK_ANCHOR_TYPES",
    "anchor_strength",
    "anchor_types_for",
    "build_anchor_meta",
    "get_active_anchors",
]
