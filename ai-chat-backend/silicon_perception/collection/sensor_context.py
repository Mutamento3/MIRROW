"""手机传感器上下文数据源 — 姿态/运动/光线/距离（AionsHome DeviceContextClassifier 移植）

2026-08-24 新增：手机重力+线性加速度+光线+距离传感器 → 手机朝向/运动/环境。
手机端 MirrowRelayService 常驻采集（亮屏注册/熄屏注销，2s 稳定判定），
本数据源每 tick 通过 mobile_sensor_context relay 拉取缓存快照。

输出 slot（照 AionsHome snapshot() 结构，各带 observed_at/since/confidence）：
  posture: face_up/face_down/portrait/landscape_left/landscape_right/tilted
  motion:  still/slight/moving/strong
  light:   dark/dim/normal/bright
  proximity: near/far
  screen:  on/off
  foreground_app: 包名
"""

import asyncio
import logging
import math
from datetime import datetime
from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


class SensorContextSource(DataSource):
    """手机传感器上下文 — 手机 relay 拉取（低频，读缓存）"""

    name = "sensor_context"
    tick_interval = 1  # 每基础 tick（60s）

    def __init__(self):
        self._last_data: dict = {}
        self._last_success_time = None
        self._last_error = None

    async def read(self) -> DataPoint:
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                self._last_error = "手机后台未连接"
                return self._data_point(available=False)

            import uuid
            result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_sensor_context", {}),
                timeout=8.0,
            )
            if not isinstance(result, dict) or not result.get("success"):
                self._last_error = str(
                    (result if isinstance(result, dict) else {}).get("content")
                    or (result if isinstance(result, dict) else {}).get("error")
                    or "传感器读取失败")[:120]
                return self._data_point(available=False)

            data = result.get("data")
            if not isinstance(data, dict):
                self._last_error = "传感器数据格式错误"
                return self._data_point(available=False)

            # 只保留已知 slot 的 value/since/observed_at，忽略未知字段
            cleaned = {}
            observed_seconds = []
            now_seconds = datetime.now().timestamp()
            for key in ("posture", "motion", "light", "proximity", "screen", "foreground_app"):
                slot = data.get(key)
                if isinstance(slot, dict) and slot.get("value"):
                    try:
                        observed = float(slot.get("observed_at"))
                    except (TypeError, ValueError):
                        continue
                    # 每个值需有自身的真实采样时间，缺失/未来/过期不能借其他 slot 续鲜。
                    if not math.isfinite(observed) or not 0 <= now_seconds - observed <= 600:
                        continue
                    cleaned[key] = {
                        "value": str(slot.get("value", ""))[:40],
                        "since": str(slot.get("since", "")),
                        "observed_at": str(slot.get("observed_at", "")),
                    }
                    observed_seconds.append(observed)
            self._last_data = cleaned
            # 一个 bundle 内各 slot 可能来自不同时刻。使用关键 slot 中最旧的
            # 实际观测时刻作为整体时效，避免“光线刚更新”把旧姿态一并续鲜。
            self._last_success_time = (
                datetime.fromtimestamp(min(observed_seconds)).isoformat()
                if observed_seconds else None
            )
            self._last_error = None if cleaned else "传感器缺少有效采样时间"
            return self._data_point(available=bool(cleaned))
        except asyncio.TimeoutError:
            self._last_error = "传感器读取超时"
            return self._data_point(available=False)
        except Exception as e:
            self._last_error = f"传感器读取异常: {str(e)[:80]}"
            logger.debug(f"SensorContextSource 读取失败: {e}")
            return self._data_point(available=False)

    def _data_point(self, available: bool) -> DataPoint:
        return DataPoint(
            source=self.name,
            data={
                "available": available,
                **({} if not available else self._last_data),
            },
            captured_at=self._last_success_time,
        )

    @property
    def connection_state(self) -> str:
        if self._last_data:
            return "connected"
        return "failed" if self._last_error else "disconnected"

    @property
    def last_error(self):
        return self._last_error
