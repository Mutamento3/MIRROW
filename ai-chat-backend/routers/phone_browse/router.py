"""刷手机模式 API 路由"""

import logging
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/phone-browse", tags=["phone-browse"])


@router.get("/mode")
async def get_phone_browse_mode():
    """获取刷手机模式状态"""
    from shared_state import get_phone_browse_mode as _get_mode
    return {"phone_browse_mode": _get_mode()}


@router.post("/mode")
async def set_phone_browse_mode(request: Request):
    """持久化确认后更新运行状态；失败不重放切换。"""
    persisted = False
    get_mode = None
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("phone_browse_mode"), bool):
            return {"ok": False, "persisted": False, "error": "刷手机开关需要布尔值"}
        mode = body["phone_browse_mode"]
        from shared_state import get_phone_browse_mode as get_mode, set_phone_browse_mode as set_mode, get_mobile_connected
        from phone_browser import get_phone_browser
        from settings_manager import set_setting

        previous = get_mode()
        browser = get_phone_browser()
        set_setting("phone_browse_mode", mode)
        persisted = True
        set_mode(mode)
        # set_active 仅初始化冷却；重复保存同一状态不延迟下一次截图。
        if browser and mode != previous:
            browser.set_active(mode and get_mobile_connected())
        logger.info("Phone browse mode %s (persisted)", mode)
        return {"phone_browse_mode": mode, "ok": True, "persisted": True}
    except Exception:
        logger.warning("Phone browse switch unconfirmed persisted=%s", persisted)
        actual = None
        if get_mode is not None:
            try:
                actual = get_mode()
            except Exception:
                pass
        return {"ok": False, "persisted": persisted, "phone_browse_mode": actual,
                "error": "刷手机切换未获确认"}


@router.post("/summon")
async def phone_browse_summon():
    """悬浮球召唤 — 重置CD，立即触发截图+推送"""
    from shared_state import get_phone_browse_mode, get_mobile_connected
    from phone_browser import get_phone_browser

    if not get_phone_browse_mode():
        return {"ok": False, "error": "刷手机模式未开启"}
    if not get_mobile_connected():
        return {"ok": False, "error": "手机未连接"}

    # 检查活跃会话（无会话时推送会失败）
    try:
        from shared_state import get_latest_session_id
        active_session = get_latest_session_id()
        if not active_session:
            import json as _json, os as _os
            _idx_path = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                "sessions", "sessions_index.json"
            )
            if _os.path.exists(_idx_path):
                with open(_idx_path, "r", encoding="utf-8") as _f:
                    _idx = _json.load(_f)
                active_session = _idx.get("current_session_id") if isinstance(_idx, dict) else None
        if not active_session:
            return {"ok": False, "error": "请先打开一个会话"}
    except Exception:
        pass  # 不因校验失败而阻止 summon

    browser = get_phone_browser()
    if not browser:
        logger.error("Phone browse summon: PhoneBrowser not initialized")
        return {"ok": False, "error": "PhoneBrowser 未初始化"}
    await browser.summon()
    logger.info("Phone browse summon triggered")
    return {"ok": True, "message": "已触发陪刷分析"}
