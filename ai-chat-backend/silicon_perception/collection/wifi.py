"""WiFi 状态数据源 — 事件驱动 + 轮询兜底

MirrowRelayService 推送 wifi_state_change 事件 → wander WS → shared_state → on_event()
正常情况下 read() 直接返回内存中的最新状态，零延迟。
超过 120s 无 push 事件 → 主动 poll 手机确认。
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)

# 全局单例引用（由 sentinel._init_sources 设置，供 main.py wander WS handler 调用）
_wifi_source: Optional["WifiSource"] = None


def get_wifi_source() -> Optional["WifiSource"]:
    return _wifi_source


def handle_wifi_relay_event(event: dict) -> None:
    """Accept one Android relay observation and update both runtime projections."""
    from shared_state import set_wifi_state

    # set_wifi_state 已同步通知全局 WifiSource；这里只调用一次，避免同一 relay
    # 消息被重复消费（虽然第二次通常会被 state_changed 门控挡住）。
    set_wifi_state(event)
    logger.info(
        "WifiSource: relay → connected=%s, kind=%s（网络标识已省略）",
        event.get("connected"),
        event.get("event_kind") or "transition",
    )


class WifiSource(DataSource):
    """WiFi 状态 — 事件驱动（手机 push）+ 轮询兜底"""

    name = "wifi_state"

    def __init__(self):
        self._current_ssid: Optional[str] = None
        self._connected: bool = False
        self._bssid: Optional[str] = None
        self._last_change_time: Optional[str] = None
        self._last_observed_time: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_event_time: float = 0.0  # monotonic，判断是否需要 poll 兜底
        self._home_ssids: set = set()
        self._consecutive_failures: int = 0
        self._transition_serial: int = 0
        self._initialized: bool = False

        # 注册为全局单例
        global _wifi_source
        _wifi_source = self

        # 从 DB/配置恢复家 SSID 列表
        self._home_ssids = self._load_home_ssids()

        # 回放可能先于初始化到达的缓存事件（shared_state 缓存）
        try:
            from shared_state import get_wifi_state
            cached = get_wifi_state()
            if cached:
                logger.info("WifiSource: 回放缓存状态（网络标识已省略）")
                self.on_event(cached)
        except Exception:
            pass

    @property
    def tick_interval(self) -> int:
        """每 tick 都读（事件驱动，read() 极轻量只读内存）。失败多次后加快轮询。"""
        return 1 if self._consecutive_failures >= 3 else 1

    def on_event(self, event: dict):
        """由 main.py wander WS handler 调用，推送 WiFi 状态变化。

        event 格式: {"ssid": "MyWiFi", "connected": true, "bssid": "aa:bb:cc:..."}
        断开时: {"connected": false, "previous_ssid": "MyWiFi"}
        """
        previous_connected = self._connected
        previous_ssid = self._current_ssid
        next_connected = bool(event.get("connected", False))
        next_ssid = event.get("ssid") if next_connected else None
        state_changed = next_connected != previous_connected or next_ssid != previous_ssid
        event_kind = str(event.get("event_kind") or "transition")

        self._connected = next_connected
        if self._connected:
            self._current_ssid = next_ssid
            self._bssid = event.get("bssid")
        else:
            self._current_ssid = None
            self._bssid = None
        observed_at = datetime.now().isoformat()
        self._last_observed_time = observed_at
        if state_changed:
            self._last_change_time = observed_at
            # 初始化同步/重连快照只建立当前基线，不伪造「刚刚连上」。旧 APK
            # 没有 event_kind，仍按 transition 兼容；但首个观测永远只建基线。
            if self._initialized and event_kind == "transition":
                self._transition_serial += 1
        self._initialized = True
        self._last_event_time = time.monotonic()
        self._last_error = None
        self._consecutive_failures = 0

        is_home = self._is_home_wifi(self._current_ssid) if self._connected else False
        logger.info(
            "WifiSource: event → connected=%s, is_home=%s, kind=%s",
            self._connected,
            is_home,
            event_kind,
        )

    async def read(self, force: bool = False) -> DataPoint:
        """返回当前 WiFi 状态。正常情况下直接读内存。

        force=True 或超过 120s 无事件 → 主动 poll 手机。
        """
        now = time.monotonic()
        stale_sec = now - self._last_event_time if self._last_event_time > 0 else 999999

        # 兜底轮询：超过 120s 没有收到 push 事件 → 主动查手机
        if force or stale_sec > 120:
            try:
                await self._poll_phone()
            except Exception:
                pass

        is_home = self._is_home_wifi(self._current_ssid) if self._connected else False

        return DataPoint(source=self.name, data={
            "wifi_connected": self._connected,
            "wifi_ssid": self._current_ssid,
            "wifi_bssid": self._bssid,
            "wifi_is_home": is_home,
            "wifi_transition_serial": self._transition_serial,
            "wifi_changed_at": self._last_change_time,
        }, captured_at=self._last_observed_time)

    async def _poll_phone(self):
        """主动通过 mobile_relay 查询手机 WiFi 状态。"""
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                self._last_error = "手机未连接"
                self._consecutive_failures += 1
                return
            import uuid
            result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_wifi_status", {}),
                timeout=10.0,
            )
            if isinstance(result, dict) and result.get("success"):
                data = result.get("data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                ssid = data.get("ssid")
                connected = data.get("connected", bool(ssid))
                bssid = data.get("bssid")

                # poll 只确认现状，不能证明变化发生在此刻，因此绝不增加 transition_serial。
                changed = (connected != self._connected or ssid != self._current_ssid)
                self._connected = connected
                self._current_ssid = ssid if connected else None
                self._bssid = bssid if connected else None
                observed_at = datetime.now().isoformat()
                self._last_observed_time = observed_at
                if changed:
                    self._last_change_time = observed_at
                self._initialized = True
                self._last_event_time = time.monotonic()
                self._consecutive_failures = 0
                self._last_error = None
                if changed:
                    logger.info(
                        "WifiSource: poll 检测到变化 → connected=%s, is_home=%s",
                        connected,
                        self._is_home_wifi(self._current_ssid) if connected else False,
                    )
                else:
                    logger.debug("WifiSource: poll 确认未变 → connected=%s", connected)
            else:
                self._consecutive_failures += 1
                err = result.get("error") or result.get("content") or "查询失败" if isinstance(result, dict) else "查询失败"
                self._last_error = str(err)[:120]
                logger.debug(f"WifiSource: poll 失败: {self._last_error}")
        except asyncio.TimeoutError:
            self._consecutive_failures += 1
            self._last_error = "WiFi 查询超时(10s)"
        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"查询异常: {str(e)[:60]}"
            logger.debug(f"WifiSource: poll 异常: {e}")

    def _is_home_wifi(self, ssid: Optional[str]) -> bool:
        """判断给定 SSID 是否为家 WiFi。"""
        if not ssid:
            return False
        # Android 返回的 SSID 带双引号（如 "MyWiFi"），去引号后匹配
        clean = ssid.strip('"')
        return clean in self._home_ssids

    def _load_home_ssids(self) -> set:
        """从 user_schedule 或 settings.json 加载家 SSID 列表。"""
        # 优先 SQLite
        try:
            from silicon_perception.recording.health_store import get_store
            schedule = get_store().get_user_schedule()
            if schedule:
                raw = schedule.get("home_wifi_ssids")
                if raw:
                    if isinstance(raw, str):
                        ssids = json.loads(raw)
                    elif isinstance(raw, list):
                        ssids = raw
                    else:
                        ssids = []
                    result = set(ssids)
                    if result:
                        logger.info("WifiSource: 已从 DB 加载 %d 个家庭网络标识", len(result))
                        return result
        except Exception:
            pass
        # 回退 settings.json
        try:
            from settings_manager import get_setting
            ss = get_setting("silicon_perception") or {}
            wifi_cfg = ss.get("wifi", {})
            raw = wifi_cfg.get("home_ssids", [])
            if raw:
                result = set(raw)
                if result:
                    logger.info("WifiSource: 已从设置加载 %d 个家庭网络标识", len(result))
                    return result
        except Exception:
            pass
        return set()

    def reload_home_ssids(self):
        """重新加载家 SSID 列表（配置变更后调用）。"""
        self._home_ssids = self._load_home_ssids()

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._last_observed_time is not None:
            return "connected"
        return "disconnected"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_change_seconds_ago(self) -> float:
        if self._last_change_time is None:
            return 999999.0
        try:
            return (datetime.now() - datetime.fromisoformat(self._last_change_time)).total_seconds()
        except Exception:
            return 999999.0

    @property
    def last_observed_seconds_ago(self) -> float:
        if self._last_observed_time is None:
            return 999999.0
        try:
            return (datetime.now() - datetime.fromisoformat(self._last_observed_time)).total_seconds()
        except Exception:
            return 999999.0
