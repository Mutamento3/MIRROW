"""键鼠空闲数据源 — 以 Raw Input 区分本地物理与远程输入。

``GetLastInputInfo`` 没有输入来源信息，不能作为远程控制场景下的本地在场
证据。来源识别由 ``raw_input_origin`` 独立负责；本数据源只把已确认的本地
物理输入暴露为既有 ``input_idle_seconds``，并另行提供远程输入 idle。

Raw Input 尚未启动、没有收到过可分类事件或平台不可用时，idle 为 ``None``。
这是一种安全降级：未知不能刷新本地活动证据，也不会把 UU 虚拟 HID 冒充成
人类伙伴在电脑前。
"""

import logging
from datetime import datetime
from typing import Optional

from .base import DataSource, DataPoint
from .raw_input_origin import get_input_origin_tracker

logger = logging.getLogger(__name__)


class InputIdleSource(DataSource):
    """键鼠空闲检测 — 本地物理输入和远程输入分开计时。"""

    name = "input_idle"

    def __init__(self, tracker=None):
        # tracker 注入仅供聚焦测试/平台适配；生产默认使用进程级单例。
        self._tracker = tracker or get_input_origin_tracker()

    async def start(self):
        """启动 Raw Input 消息窗口；不等待第一笔输入。"""
        try:
            self._tracker.start()
        except Exception as exc:
            logger.warning("Raw Input 启动失败: %s", exc)

    async def stop(self):
        """释放 Raw Input 消息窗口。"""
        try:
            self._tracker.stop()
        except Exception:
            logger.debug("Raw Input 停止失败", exc_info=True)

    async def read(self) -> DataPoint:
        # read 也调用 start，兼容未经过 Sentinel.start() 的独立使用场景。
        try:
            self._tracker.start(wait_seconds=0.25)
        except Exception as exc:
            logger.debug("Raw Input 读取前启动失败: %s", exc)

        try:
            state = self._tracker.snapshot()
        except Exception as exc:
            logger.warning("Raw Input 状态读取失败: %s", exc)
            state = None

        physical = state.physical_input_idle if state else None
        remote = state.remote_input_idle if state else None
        available = bool(state and state.available)
        data = {
            # 既有字段：只代表本地物理输入 idle。
            "input_idle_seconds": round(physical, 1) if physical is not None else None,
            "physical_input_idle": round(physical, 1) if physical is not None else None,
            "physical_input_idle_seconds": round(physical, 1) if physical is not None else None,
            "remote_input_idle": round(remote, 1) if remote is not None else None,
            "remote_input_idle_seconds": round(remote, 1) if remote is not None else None,
            "physical_available": available,
            "input_origin_available": available,
            "last_input_origin": state.last_origin.value if state and state.last_origin else None,
        }
        return DataPoint(
            source=self.name,
            data=data,
            # 这是对来源状态的实时读取时间；具体事件时间在 tracker 内以
            # monotonic 记录，避免系统时钟回拨影响 idle。
            captured_at=datetime.now().isoformat(),
        )

    @staticmethod
    def _get_idle_seconds() -> Optional[float]:
        """兼容旧调用方的接口。

        旧的 ``GetLastInputInfo`` 结果无法区分 UU 虚拟 HID，不再作为本地
        键鼠活动返回。调用方如需当前本地 idle，应读取本数据源的快照字段。
        """
        return None

    @property
    def connection_state(self) -> str:
        """Raw Input 注册成功即表示来源通道可用。"""
        try:
            return "connected" if self._tracker.available else "failed"
        except Exception:
            return "failed"


__all__ = ["InputIdleSource"]
