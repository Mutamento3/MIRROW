"""当前前台App数据源 — 通过手机 relay 快速轮询

每 2 分钟通过 mobile_relay 调用 mobile_current_app，
获取手机当前/最近前台App包名。
"""
import asyncio
import logging
import math
from datetime import datetime
from .base import DataSource, DataPoint
from .app_name_map import is_filtered_package, resolve_app_name

logger = logging.getLogger(__name__)


class CurrentAppSource(DataSource):
    """手机当前前台App — 2min 轮询"""

    name = "current_app"
    tick_interval = 2  # 每 2 个基础 tick 采集一次（2min）

    def __init__(self):
        self._last_package: str = ""
        self._last_screen_on: bool = False
        self._consecutive_failures: int = 0
        self._last_success_time = None
        self._last_error = None

    async def read(self) -> DataPoint:
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                self._consecutive_failures += 1
                self._last_error = "手机后台未连接"
                return DataPoint(source=self.name, data={
                    "available": False,
                    "last_package": self._last_package or None,
                }, captured_at=self._last_success_time)

            import uuid
            result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_current_app", {}),
                timeout=8.0,
            )
            if not isinstance(result, dict) or not result.get("success"):
                self._consecutive_failures += 1
                failure = result if isinstance(result, dict) else {}
                self._last_error = str(failure.get("error") or failure.get("content") or "当前应用读取失败")[:120]
                logger.warning(f"CurrentAppSource 读取失败: {self._last_error} (result={str(result)[:200]})")
                return DataPoint(source=self.name, data={
                    "available": False,
                    "last_package": self._last_package or None,
                }, captured_at=self._last_success_time)

            data = result.get("data", {})
            if not isinstance(data, dict) or data.get("available", True) is False:
                self._consecutive_failures += 1
                self._last_error = str(data.get("error") or "当前应用观测不可用")[:120] if isinstance(data, dict) else "当前应用数据格式错误"
                return DataPoint(source=self.name, data={
                    "available": False,
                    "last_package": self._last_package or None,
                }, captured_at=self._last_success_time)
            pkg = data.get("package", "")
            screen_on = data.get("screen_on")
            if not isinstance(screen_on, bool):
                screen_on = None
            observed_at_ms = data.get("observed_at_ms")
            observed_time = None
            try:
                seconds = float(observed_at_ms) / 1000
                if (not isinstance(observed_at_ms, bool) and math.isfinite(seconds)
                        and 0 < seconds <= datetime.now().timestamp() + 60):
                    observed_time = datetime.fromtimestamp(seconds).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                pass
            foreground_available = (
                screen_on is True and bool(pkg) and observed_time is not None
                and data.get("foreground_available", True) is True
            )
            if not foreground_available:
                pkg = ""
            filtered_pkg = pkg if is_filtered_package(pkg) else ""
            if filtered_pkg:
                pkg = ""
                app_name = None
            else:
                app_name = (resolve_app_name(pkg) or data.get("app_name") or pkg) if pkg else None
            if pkg:
                self._last_package = pkg
            self._last_screen_on = screen_on
            self._consecutive_failures = 0
            self._last_error = None
            # 前台必须保留设备时间；仅屏幕状态可使用这次请求的时间。
            self._last_success_time = (
                observed_time if foreground_available else
                datetime.now().isoformat() if screen_on is not None else None
            )
            return DataPoint(source=self.name, data={
                "available": foreground_available or screen_on is not None,
                "foreground_available": foreground_available,
                "package": pkg,
                "screen_on": screen_on,
                "app_name": app_name,
                "observed_window_package": (data.get("observed_window_package") or filtered_pkg or pkg) if foreground_available else None,
                "overlay_package": data.get("overlay_package") if foreground_available else None,
                "underlying_package": data.get("underlying_package") if foreground_available else None,
                "foreground_resolution": data.get("foreground_resolution") or "legacy_relay",
                "observed_at_ms": data.get("observed_at_ms") or None,
            }, captured_at=self._last_success_time)
        except asyncio.TimeoutError:
            self._consecutive_failures += 1
            self._last_error = "当前应用读取超时"
            return DataPoint(source=self.name, data={
                "available": False, "last_package": self._last_package or None,
            }, captured_at=self._last_success_time)
        except Exception as e:
            self._consecutive_failures += 1
            self._last_error = f"当前应用读取异常: {str(e)[:80]}"
            logger.debug(f"CurrentAppSource 读取失败: {e}")
            return DataPoint(source=self.name, data={
                "available": False, "last_package": self._last_package or None,
            }, captured_at=self._last_success_time)

    @property
    def connection_state(self) -> str:
        if self._consecutive_failures >= 3:
            return "failed"
        if self._last_success_time:
            return "connected"
        return "disconnected"

    @property
    def last_error(self):
        return self._last_error
