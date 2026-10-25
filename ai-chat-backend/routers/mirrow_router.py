"""MIRROW 心跳与健康检查路由 — 从 main.py 提取"""

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mirrow"])


@router.post("/api/mirrow/heartbeat")
async def api_mirrow_heartbeat(request: dict):
    """前端定期上报 MIRROW 页面可见性（用于漫想模式判断用户是否在 MIRROW 页面）。"""
    import time
    from shared_state import (
        get_mirrow_page_visible, set_mirrow_page_visible,
        set_mirrow_page_last_seen,
        get_night_mode_active, get_music_mode_active,
        get_phone_browse_mode,
    )
    was_visible = get_mirrow_page_visible()
    set_mirrow_page_visible(request.get("visible", False))
    set_mirrow_page_last_seen(time.monotonic())
    if not was_visible and get_mirrow_page_visible():
        from wander_manager.user_status import check_wakeup
        if check_wakeup():
            try:
                from wander_manager.manager import get_wander_manager
                wm = get_wander_manager()
                if wm:
                    wm.set_user_status("idle", "", source="wakeup")
                else:
                    from wander_manager.user_status import set_user_status_combined, UserStatus
                    set_user_status_combined(UserStatus.IDLE, "", source="wakeup")
                    from health_tracker.tracker import get_health_tracker
                    get_health_tracker().on_status_change("sleeping", "idle", "")
            except Exception:
                pass
    return {
        "status": "ok",
        "night_mode": get_night_mode_active(),
        "music_mode": get_music_mode_active(),
        "phone_browse_mode": get_phone_browse_mode(),
    }


@router.get("/api/health/sessions")
async def check_sessions_health():
    """会话健康检查"""
    from session_manager import get_global_session_manager
    import os as _os

    session_manager = get_global_session_manager()
    sessions_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "sessions")
    index_path = _os.path.join(sessions_dir, "sessions_index.json")
    index_exists = _os.path.exists(index_path)

    try:
        sessions = await session_manager.get_all_sessions()
        sessions_count = len(sessions) if sessions else 0
        data_files = [f for f in _os.listdir(sessions_dir) if f.endswith('.json') and f != 'sessions_index.json'] if _os.path.isdir(sessions_dir) else []
        return {
            "status": "ok",
            "sessions_count": sessions_count,
            "data_files": len(data_files),
            "index_exists": index_exists,
            "data_dir": sessions_dir,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "index_exists": index_exists,
        }
