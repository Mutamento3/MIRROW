"""Historical API health; never generate content as a background probe."""
from pathlib import Path
import sqlite3
from .audit import DB_PATH, now_iso


def snapshot(config, environment, path: Path = DB_PATH):
    rows = []
    db = None
    try:
        if path.exists():
            db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            db.row_factory = sqlite3.Row
        for station in config.stations:
            models = []
            for model in station.models:
                from .availability import readiness
                availability = readiness({'station': station.model_dump(), 'model': model.model_dump(),
                    'api_key': environment.get(station.key_env, ''), 'availability_managed': True},
                    terminal=model.id in {'direct_pro', 'direct_flash'})
                last = None
                if db:
                    try:
                        last = db.execute("SELECT status,error_code,started_at,ended_at,duration_ms,http_status FROM attempts WHERE station_id=? AND source_id=? AND model=? AND node!='connection_test' ORDER BY started_at DESC LIMIT 1", (station.id, model.id, model.model)).fetchone()
                    except sqlite3.Error:
                        pass
                models.append({"id": model.id, "label": model.label, "model": model.model,
                    "last_call": dict(last) if last else None, "enabled": model.enabled,
                    "availability": availability})
            rows.append({"id": station.id, "name": station.name, "enabled": station.enabled,
                         "key_configured": bool(environment.get(station.key_env, "").strip()), "models": models})
    finally:
        if db:
            db.close()
    return {"stations": rows, "updated_at": now_iso()}
