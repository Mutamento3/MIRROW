"""回写桥 — 把 StateEngine 的 StateTransition 落到 user_status（经全管线）。

复用 WanderManager.set_user_status —— 它已经是全管线：写状态 + HealthTracker
+ WS 广播。引擎回写走同一条路，不绕过，保证所有下游一致。

额外：推送二维 status_change 事件给前端（presence/activity/证据），供状态面板实时更新
（现状聊天前端无此推送，这是补的缺口）。ws_push 回调由 main.py 注入。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .evidence import StateTransition

logger = logging.getLogger(__name__)

# 二维 status_change 前端推送回调（main.py 注入）
_ws_push: Optional[Callable[[dict], None]] = None


def set_ws_push(cb: Optional[Callable[[dict], None]]):
    global _ws_push
    _ws_push = cb


def apply_transition(trans: StateTransition) -> bool:
    """应用一次状态切换：回写 user_status（全管线）+ 推送前端。返回是否成功。"""
    legacy = trans.to_legacy
    # 1 回写 user_status（复用全管线：HealthTracker/WS 广播）
    try:
        from wander_manager.manager import get_wander_manager
        get_wander_manager().set_user_status(legacy, "", source="auto")
    except Exception as e:
        logger.error(f"bridge 回写 user_status 失败: {e}")
        return False

    # 1.5 显式同步 StatusMeta（处理 legacy 不变但 location 变的情况）
    try:
        from wander_manager.user_status import set_status_meta, StatusMeta, _resolve_location_for_full_hold
        meta = StatusMeta.from_state_vector(trans.to_state, "auto")
        # FULL_HOLD 状态：GPS 新鲜值覆盖 location
        override = _resolve_location_for_full_hold(legacy)
        if override:
            meta.location = override
        set_status_meta(meta)
        from shared_state import set_current_status_meta
        set_current_status_meta(meta.to_dict())
    except Exception as e:
        logger.debug(f"bridge 同步 StatusMeta 失败: {e}")

    # 2 推送二维 status_change 给前端（presence/activity/证据链）
    if _ws_push:
        try:
            _ws_push({
                "type": "user_status_change",
                "legacy": legacy,
                "presence": trans.to_state.presence.value,
                "activity": trans.to_state.activity.value,
                "rest_mode": trans.to_state.rest_mode.value,
                "source": trans.source,
                "reason_code": trans.reason_code,
                "human_hint": trans.human_hint,
                "at": trans.at,
                "evidence": [
                    {"source": e.source, "value": str(e.value), "tier": e.tier}
                    for e in trans.evidence
                ],
            })
        except Exception as e:
            logger.debug(f"bridge 前端推送失败: {e}")
    return True
