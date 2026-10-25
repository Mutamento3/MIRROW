"""StateEngine — 状态引擎核心。

每 tick 消费 sentinel 的 DataSnapshot → 仲裁 → 防抖 → 产出 StateTransition。

来源三层（source）+ 声明保护 + 意图锚：
- explicit（用户点按钮）/ latent（聊天口头意图+印证，阶段2 接入）→ **受保护**：引擎不自动切，
  只有正向铁证（检测到另一 activity / 家庭 WiFi 出/回）才覆盖；偏差留给查岗（阶段2）。
- auto（引擎凭铁证填的）→ 引擎自由更新（含切出），客观标注、天然一致、无需查岗。
- 完全 hold（eating/bathing/other/sleeping/napping）→ 引擎永不自动改（无法物理验证）。
- 意图锚：最近一次 explicit/latent 声明；被 auto 切走后行为回归锚点 → 低门槛恢复为受保护声明。

区分"用户改" vs "引擎改"：引擎回写时记 `_last_written`；`snapshot.user_status != _last_written`
→ 用户手动改 → explicit。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .model import Activity, Presence, RestMode, StateVector, from_legacy
from .arbiter import arbitrate
from .transitions import Debouncer
from .evidence import StateTransition
from .thresholds import get_thresholds
from .process_catalog import get_catalog

logger = logging.getLogger(__name__)

# 完全 hold（物理无关的声明状态，引擎永不自动改；切出留给阶段2/用户）
_FULL_HOLD = {"eating", "bathing", "other", "sleeping"}  # napping 允许自动切出


class StateEngine:
    def __init__(self, initial_legacy: str = "idle"):
        self._t = get_thresholds()
        self._catalog = get_catalog()
        self._deb = Debouncer(from_legacy(initial_legacy))
        # 播种：非 idle 视为用户曾声明（explicit 受保护）；idle 视为空白（auto 可自由管理）
        is_declared = initial_legacy != "idle"
        self._committed_source = "explicit" if is_declared else "auto"  # explicit/latent/auto
        self._last_written = initial_legacy   # 引擎上次回写值（区分用户改 vs 引擎改）

    # ---------- 内部 ----------
    def _required_sec(self, candidate: StateVector) -> float:
        """候选提交所需持续秒数。coding 需长确认；gaming 期间切 coding 更长。"""
        if candidate.activity == Activity.CODING:
            if self._deb.committed.activity == Activity.GAMING:
                return self._t.resolve("gaming_coding_sustain_sec")
            return self._t.resolve("coding_sustain_sec")
        return self._t.resolve("min_dwell_sec")

    def _protected(self) -> bool:
        """当前 committed 是否受声明保护（explicit/latent 来源）。"""
        return self._committed_source in ("explicit", "latent")

    def _seed_declaration(self, legacy_status: str, source: str, foreground_exe: Optional[str]):
        """播种一次用户声明（explicit/latent）：force_commit + 更新来源。"""
        sv = from_legacy(legacy_status)
        self._deb.force_commit(sv)
        self._committed_source = source
        self._last_written = legacy_status
        if legacy_status == "gaming" and foreground_exe:
            self._catalog.record_declared_gaming(foreground_exe)

    # ---------- 主循环 ----------
    def tick(self, snapshot) -> Optional[StateTransition]:
        """处理一次快照，返回状态切换（无变化返回 None）。"""
        now_iso = getattr(snapshot, "timestamp", None) or datetime.now().isoformat()

        # ── 1 reconcile：检测用户手动改（explicit 声明）──
        declared = getattr(snapshot, "user_status", None)
        if declared and declared != self._deb.committed.to_legacy():
            if declared != self._last_written:
                # 用户在别处手动改了状态（不是引擎写的）→ explicit 声明，受保护 + 更新意图锚
                self._seed_declaration(declared, "explicit", getattr(snapshot, "foreground_exe", None))
            # else: declared == _last_written 但 committed 未同步（罕见）→ 交由下方仲裁自然收敛

        committed = self._deb.committed
        legacy_now = committed.to_legacy()

        # ── 2 完全 hold 的声明状态：不碰 ──
        if legacy_now in _FULL_HOLD:
            return None

        # ── 3 仲裁 ──
        candidate = arbitrate(snapshot, self._t, self._catalog)

        # 3.0 不确定事实只能保持现状，绝不能借 legacy 映射写成 idle。
        if not candidate.is_definite():
            return None

        # 3.1 WiFi 回家门控：仅在当前为 "out" 时才切换。
        #     如果当前已是 home 侧状态，重复的家庭 WiFi 确认无需产生切换。
        if candidate.reason_code == "wifi_home" and legacy_now != "out":
            return None  # 已在家，无需因 WiFi 重连而切换
        if legacy_now == "out" and candidate.reason_code != "wifi_home":
            return None  # 外出只能由家庭 WiFi 铁证切回，PC/手机活动不代表到家

        # 家庭 WiFi 本身就是位置权威，Android 侧连接/断开已经做过防抖；
        # 不再叠加通用 120s 驻留，否则同一个 tick 的上下文会同时出现“在家”和“out”。
        if candidate.reason_code in ("wifi_home", "wifi_not_home"):
            projected = candidate.to_legacy()
            if projected == legacy_now:
                return None
            self._deb.force_commit(candidate)
            self._committed_source = "auto"
            self._last_written = projected
            trans = StateTransition.build(
                from_legacy=legacy_now,
                to_state=candidate,
                at=now_iso,
                source="auto",
            )
            logger.info(f"StateEngine: {trans.from_legacy} → {trans.to_legacy} ({trans.reason_code})")
            return trans

        # 3.2 napping 自动切出：检测到物理活动→切idle
        if committed.rest_mode == RestMode.NAPPING and candidate.confidence >= 1.0:
            wake_sv = StateVector(presence=candidate.presence, confidence=1.0, reason_code="nap_wake")
            self._deb.force_commit(wake_sv)
            self._committed_source = "auto"
            self._last_written = wake_sv.to_legacy()
            if wake_sv.to_legacy() != legacy_now:
                logger.info(f"StateEngine: napping 切出 → {wake_sv.to_legacy()}")
                return StateTransition.build(from_legacy=legacy_now, to_state=wake_sv, at=now_iso, source="auto")
            return None

        # 3.3 声明保护（explicit/latent）：activity 不自动切，presence 仍自动跟踪。
        #     "你在打游戏"or"在写代码"是最高权威——切窗口/挂机/afk 不改 activity。
        #     但物理位置（out/idle/away）跟 activity 无关——出去就是出去，回来就是回来。
        #     presence-only 更新不覆盖 _committed_source——否则一次 AFK 就把保护降级为 auto。
        #
        #     例外：reason_code == "wifi_home" — WiFi 路由器是物理锚点，100% 可靠。
        #     手动声明的 "out" 在 WiFi 铁证面前要让路（否则出去了回不来）。
        presence_only = False
        if self._protected():
            if candidate.activity not in (Activity.NONE, committed.activity):
                return None  # 铁证新 activity（如 coding→gaming）→ 不切
            # activity：保留你的声明
            if candidate.activity == Activity.NONE and committed.activity != Activity.NONE:
                candidate.activity = committed.activity
            # presence：你声明 out→不自动改（防 stale GPS/键鼠误覆盖）
            #   例外：WiFi 回家铁证（路由器物理锚点，零误判）→ 允许覆盖
            if committed.presence not in (Presence.UNKNOWN,):
                if candidate.reason_code in ("wifi_home", "wifi_not_home"):
                    pass  # WiFi 铁证：允许覆盖手动声明的 presence
                else:
                    candidate.presence = committed.presence
            presence_only = True  # protected 下走到提交的都是 presence-only 更新

        # 3.4 防抖提交
        req = self._required_sec(candidate)
        new_state, changed = self._deb.propose(candidate, req, now_iso=now_iso)
        if not changed:
            return None

        # legacy 投影无变化（如 UNKNOWN→AT_COMPUTER 都投影为 idle）→ 内部已提交，不外发
        if new_state.to_legacy() == legacy_now:
            if not presence_only:
                self._committed_source = "auto"
            return None

        if not presence_only:
            self._committed_source = "auto"
        self._last_written = new_state.to_legacy()
        trans = StateTransition.build(from_legacy=legacy_now, to_state=new_state, at=now_iso, source="auto")
        logger.info(f"StateEngine: {trans.from_legacy} → {trans.to_legacy} ({trans.reason_code})")
        return trans

    # ---------- 外部声明入口 ----------
    def on_manual_declare(self, legacy_status: str, foreground_exe: Optional[str] = None):
        """用户手动声明状态（API 层调用）。explicit 受保护 + 更新意图锚。"""
        self._seed_declaration(legacy_status, "explicit", foreground_exe)

    def on_latent_declare(self, legacy_status: str, foreground_exe: Optional[str] = None):
        """潜在声明（聊天口头意图+传感器印证，阶段2 由消息意图提取管道调用）。

        受保护同 explicit，但来源标 latent 以便区分/审计。
        """
        self._seed_declaration(legacy_status, "latent", foreground_exe)

    def current(self) -> StateVector:
        return self._deb.committed

    def current_source(self) -> str:
        return self._committed_source


_engine: Optional[StateEngine] = None


def get_state_engine() -> StateEngine:
    global _engine
    if _engine is None:
        # 用当前 user_status 播种，避免启动时与声明打架
        try:
            from wander_manager.user_status import get_user_status
            initial = get_user_status().value
        except Exception:
            initial = "idle"
        _engine = StateEngine(initial_legacy=initial)
    return _engine
