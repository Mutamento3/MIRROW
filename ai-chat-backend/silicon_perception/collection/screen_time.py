"""屏幕使用时长数据源 — 通过手机 relay 定期轮询

每 30 分钟通过 mobile_relay 调用 mobile_usage，
获取当日屏幕总时长 + Top App 分布。
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .base import DataSource, DataPoint
from .app_name_map import CONFIRMED_GAME_PACKAGES

logger = logging.getLogger(__name__)

# 手机 App 包名 → 类别映射。这里只接受精确包名；同一厂商可以同时有游戏、
# 视频、音乐和远控 App，前缀匹配会把横屏内容误写成“手机游戏”。
_PACKAGE_CATEGORY = {
    "com.tencent.mobileqq": "social",
    "com.tencent.mm": "social",
    "com.sina.weibo": "social",
    "com.ss.android.ugc.aweme": "entertainment",
    "com.tencent.qqlive": "entertainment",
    "tv.danmaku.bili": "entertainment",
    "com.mirrow.app": "mirrow",
    "com.android.chrome": "browser",
    "com.microsoft.office": "work",
}
for _game_package in CONFIRMED_GAME_PACKAGES:
    _PACKAGE_CATEGORY[_game_package] = "game"


def logical_date(now: Optional[datetime] = None) -> str:
    """Return the 05:00-local logical date used by phone health totals."""
    current = now or datetime.now()
    if current.hour < 5:
        current -= timedelta(days=1)
    return current.strftime("%Y-%m-%d")


class ScreenTimeSource(DataSource):
    """屏幕使用时长 — 手机 relay 每 30 分钟轮询"""

    name = "screen_time"
    tick_interval = 10  # 每 10 个基础 tick 采集一次（10×60s=10min）

    def __init__(self):
        self._consecutive_failures: int = 0
        self._last_data_time: Optional[str] = None  # 首次成功采集时间
        self._last_error: Optional[str] = None

    async def read(self) -> DataPoint:
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                self._consecutive_failures += 1
                self._last_error = "手机后台未连接"
                return DataPoint(source=self.name, data={"available": False})

            import uuid
            result = await asyncio.wait_for(relay(str(uuid.uuid4()), "mobile_usage", {}), timeout=15.0)
            if not result or not result.get("success"):
                self._consecutive_failures += 1
                self._last_error = result.get("error", "unknown") if result else "no_response"
                return DataPoint(source=self.name, data={
                    "available": False,
                    "error": self._last_error,
                })

            data = result.get("data", {})
            if not isinstance(data, dict) or data.get("available", True) is False:
                self._consecutive_failures += 1
                self._last_error = str(data.get("error") or "手机使用统计不可用")[:120] if isinstance(data, dict) else "手机使用统计格式错误"
                return DataPoint(source=self.name, data={
                    "available": False,
                    "error": self._last_error,
                    "logical_date": data.get("logical_date") if isinstance(data, dict) else None,
                })
            total_min = data.get("total_minutes", 0)
            top_apps = data.get("top_apps", [])
            # 兼容旧后台 relay 的 {apps: {package: minutes}}，新版两条路径统一为 top_apps。
            if not top_apps and isinstance(data.get("apps"), dict):
                top_apps = [
                    {"package": package, "minutes": minutes}
                    for package, minutes in data["apps"].items()
                ]
                top_apps.sort(key=lambda item: item.get("minutes", 0), reverse=True)

            # 分类聚合
            cat_minutes = {}
            for app in (top_apps or []):
                pkg = app.get("package", "")
                minutes = app.get("minutes", app.get("duration_minutes", 0))
                cat = _classify_app(pkg)
                cat_minutes[cat] = cat_minutes.get(cat, 0) + minutes

            self._consecutive_failures = 0
            self._last_error = None
            self._last_data_time = datetime.now().isoformat()
            return DataPoint(source=self.name, data={
                "available": True,
                "screen_time_minutes": total_min,
                "top_apps": top_apps[:10],
                "category_minutes": cat_minutes,
                "top_app_category": max(cat_minutes, key=cat_minutes.get) if cat_minutes else "other",
                "logical_date": data.get("logical_date") or logical_date(),
                "window_start_ms": data.get("window_start_ms"),
                "source": data.get("source"),
            }, captured_at=self._last_data_time)
        except asyncio.TimeoutError:
            self._consecutive_failures += 1
            self._last_error = "手机使用统计读取超时"
            return DataPoint(source=self.name, data={"available": False, "error": "timeout"})
        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"手机使用统计异常: {str(e)[:80]}"
            logger.debug(f"ScreenTimeSource 读取失败: {e}")
            return DataPoint(source=self.name, data={"available": False, "error": str(e)})

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._last_data_time is not None:
            return "connected"
        return "disconnected"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error


def _classify_app(package: str) -> str:
    """包名 → 大类。未知身份保持 other，不做厂商级猜测。"""
    return _PACKAGE_CATEGORY.get(package, "other")
