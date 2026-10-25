"""信号仲裁器 — 100% 铁律的落地核心。

信号分级：
- T1 物理铁证：家庭 WiFi、键鼠 idle、PC 前台进程名、NFC（future）
- T2 设备活动：手机前台 App、屏幕方差、behavior_state
- T3 用户声明：user 手动 status

100% 铁律 = 仲裁输出规则：只有"有 T1 铁证 且 无矛盾"才输出确定状态（confidence=1.0），
否则输出 presence=unknown（confidence=0），绝不猜。

时效：PC 本地信号（idle/screen/foreground_exe）每 tick 同步新采，无 field_ts，present 即新鲜；
手机信号（wifi/current_app）有 field_ts，用 snapshot.is_stale() 判陈旧。
"""

from __future__ import annotations

import logging
from typing import Optional

from .model import Presence, Activity, StateVector, SignalEvidence

logger = logging.getLogger(__name__)

# 手机信号最大有效期（约 3× 轮询间隔）
_WIFI_MAX_AGE = 300       # WiFi 事件驱动 → 5min 无事件视为陈旧
_CURRENT_APP_MAX_AGE = 360  # current_app 2min 轮询 → 6min


def arbitrate(snapshot, thresholds, catalog) -> StateVector:
    """从一次快照产出候选 StateVector（instantaneous，铁证或 unknown）。

    只做 presence 自动 + coding/gaming activity 候选。sleeping/napping（声明）由 engine 处理。
    activity=coding 的"持续时长"要求由 engine 的防抖器落实，本函数只给瞬时候选。
    """
    t = thresholds
    idle_active = t.resolve("idle_active_sec")
    idle_away = t.resolve("idle_away_sec")

    # ---- PC 本地信号（present 即新鲜）----
    idle = getattr(snapshot, "input_idle_seconds", None)
    screen_active = getattr(snapshot, "screen_active", None)
    fg_exe = getattr(snapshot, "foreground_exe", None)
    pc_available = idle is not None
    pc_active = pc_available and idle < idle_active

    # ---- 手机信号（查时效）----
    mobile_on = None
    mobile_app = None
    mobile_pkg = None
    if not snapshot.is_stale("current_app", _CURRENT_APP_MAX_AGE):
        mobile_on = getattr(snapshot, "mobile_screen_on", None)
        mobile_app = getattr(snapshot, "mobile_app_name", None)
        mobile_pkg = getattr(snapshot, "mobile_app_package", None)
    phone_active = mobile_on is True and mobile_app is not None
    # ---- WiFi 信号（查时效）----
    wifi_is_home = None
    if not snapshot.is_stale("wifi", _WIFI_MAX_AGE):
        wifi_is_home = getattr(snapshot, "wifi_is_home", None)

    ev = []

    # ── 位置切换只认家庭 WiFi ──
    # 服务器和路由器共处同一本地供电边界：服务运行时，手机是否连接家庭
    # WiFi 就是「在家/外出」的唯一自动位置事实。GPS 只供外出摘要描述。
    if wifi_is_home is True:
        ev.append(SignalEvidence("wifi", "home_ssid_connected", 1, snapshot.field_ts.get("wifi")))
        # 家庭 WiFi 只回答“是否在家”。从 out 回家时按产品约定落到 idle，
        # 不能顺手用电脑前台把这次位置切换投影成 coding/gaming。
        return StateVector(
            presence=Presence.AT_COMPUTER,
            confidence=1.0,
            evidence=ev,
            reason_code="wifi_home",
        )
    if wifi_is_home is False:
        ev.append(SignalEvidence("wifi", "home_ssid_not_connected", 1, snapshot.field_ts.get("wifi")))
        return StateVector(
            presence=Presence.OUT,
            confidence=1.0,
            evidence=ev,
            reason_code="wifi_not_home",
        )

    # ── AT_COMPUTER：键鼠活跃（T1 输入时序铁证）──
    if pc_active:
        ev.append(SignalEvidence("input_idle", idle, 1))
        sv = StateVector(presence=Presence.AT_COMPUTER, confidence=1.0, evidence=ev,
                         reason_code="pc_active")
        _apply_activity(sv, fg_exe, mobile_pkg, catalog, ev, pc_active, phone_active)
        return sv

    # ── ON_PHONE：手机在用 + PC 不活跃 ──
    if phone_active and (not pc_available or idle >= idle_active):
        ev.append(SignalEvidence("current_app", mobile_app, 2, snapshot.field_ts.get("current_app")))
        sv = StateVector(presence=Presence.ON_PHONE, confidence=1.0, evidence=ev,
                         reason_code="phone_active")
        _apply_activity(sv, fg_exe, mobile_pkg, catalog, ev, pc_active, phone_active)
        return sv

    # ── AWAY：键鼠长空闲 + 屏幕不活跃 + 手机灭屏 ──
    if pc_available and idle >= idle_away and screen_active is not True:
        phone_off = (mobile_on is not True)
        if phone_off:
            ev.append(SignalEvidence("input_idle", idle, 1))
            return StateVector(presence=Presence.AWAY, confidence=1.0, evidence=ev,
                               reason_code="idle_and_devices_off")

    # ── 其余：无法 100% 确定 ──
    return _unknown("insufficient_or_ambiguous")


def _apply_activity(sv: StateVector, fg_exe, mobile_pkg, catalog, ev, pc_active, phone_active):
    """在已确定 presence 的基础上叠加 activity 候选（coding/gaming）。

    coding 仅在 PC 活跃且前台程序明确匹配时成立；gaming 也必须来自
    PC 或手机的当前前台。后台残留的游戏进程不是「正在玩」的证据。
    activity 的持续时长要求由 engine 防抖器落实，这里只标瞬时候选。
    """
    # PC 前台分类
    if pc_active and fg_exe:
        cls = catalog.classify(fg_exe)
        if cls == "coding":
            sv.activity = Activity.CODING
            ev.append(SignalEvidence("foreground_exe", fg_exe, 1))
            return
        if cls == "gaming":
            sv.activity = Activity.GAMING
            ev.append(SignalEvidence("foreground_exe", fg_exe, 1))
            return
    # 手机前台分类（手游）
    if phone_active and mobile_pkg:
        cls = catalog.classify(mobile_pkg)
        if cls == "gaming":
            sv.activity = Activity.GAMING
            ev.append(SignalEvidence("mobile_app_package", mobile_pkg, 2))


def _unknown(reason: str) -> StateVector:
    return StateVector(presence=Presence.UNKNOWN, confidence=0.0, reason_code=reason)
