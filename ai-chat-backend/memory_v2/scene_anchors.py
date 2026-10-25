"""Rebuildable scene navigation evidence, never a copy of event/raw content."""

import asyncio
import sqlite3

from .legacy_archive import get_legacy_archive


def load_scene_anchors_sync() -> list[dict]:
    archive = get_legacy_archive()
    result = archive.list_buckets() if archive.root.is_dir() else []
    if archive.read_errors:
        raise RuntimeError("legacy scene anchors are incomplete")
    from .context_shadow import recall_service_snapshot
    with recall_service_snapshot() as (service, excluded):
        source_ids = sorted({message_id for item in service.index.load_documents()
                             if item.source_layer in {"event", "period"}
                             for message_id in item.source_message_ids
                             if message_id not in excluded})
        path = service.source.db_path
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "conversation_scenes" not in tables:
                return result
            scene_ids = set()
            for offset in range(0, len(source_ids), 400):
                group = source_ids[offset:offset+400]
                placeholders = ",".join("?" for _ in group)
                scene_ids.update(row[0] for row in conn.execute(
                    "SELECT DISTINCT s.id FROM conversation_scenes s JOIN conversation_messages m "
                    "ON m.session_id=s.session_id AND m.active_date=s.active_date "
                    "AND julianday(m.timestamp)>=julianday(s.start_ts) "
                    "AND julianday(m.timestamp)<=julianday(s.end_ts) "
                    f"WHERE m.message_id IN ({placeholders})", group))
        finally:
            conn.close()
        result.extend({"id": f"v2-scene:{value}", "metadata": {"source_scene_id": value},
                       "content": ""} for value in sorted(scene_ids))
        return result


async def load_scene_anchors() -> list[dict]:
    return await asyncio.to_thread(load_scene_anchors_sync)
