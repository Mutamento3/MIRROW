"""深夜模式路由 — 跨设备同步夜间模式状态"""
import logging
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/night-mode", tags=["night-mode"])


@router.get("")
async def get_night_mode():
    from shared_state import get_night_mode_active
    return {"night_mode": get_night_mode_active()}


@router.post("")
async def set_night_mode(request: Request):
    from shared_state import get_night_mode_active, set_night_mode_active
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("night_mode"), bool):
            return {"ok": False, "error": "深夜开关需要布尔值"}
        set_night_mode_active(body["night_mode"])
        return {"night_mode": get_night_mode_active(), "ok": True}
    except Exception:
        logger.warning("Night mode switch unconfirmed")
        actual = None
        try:
            actual = get_night_mode_active()
        except Exception:
            pass
        return {"night_mode": actual, "ok": False, "error": "深夜切换未获确认"}
