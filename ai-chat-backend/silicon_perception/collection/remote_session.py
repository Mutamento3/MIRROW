"""远程控制能力数据源 — 检测远程控制软件是否在运行。

进程枚举只回答“远程控制软件是否常驻”，不能证明当前存在远程控制会话；
活动 UU/RDP 输入由 ``raw_input_origin`` 根据 Raw Input 设备路径单独确认。
此数据源只作能力标注，避免把常驻后台进程描述成正在远控。

PC 本机进程枚举（psutil），零手机依赖，每基础 tick 采集。
"""

import logging
from datetime import datetime
from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)


class RemoteSessionSource(DataSource):
    """远程控制软件运行检测 — PC 进程枚举（能力标注，不是活动会话）"""

    name = "remote_session"
    tick_interval = 1  # 每基础 tick（60s）

    def __init__(self):
        self._last_active: bool = False
        self._last_success_time = None
        self._last_error = None

    async def read(self) -> DataPoint:
        try:
            from silicon_perception.state.process_catalog import get_catalog
            active = get_catalog().is_remote_control_running()
            self._last_active = active
            self._last_success_time = datetime.now().isoformat()
            self._last_error = None
            return DataPoint(
                source=self.name,
                data={
                    # 保留旧字段名，避免消费者断裂；语义是“软件能力存在”。
                    "remote_control_active": active,
                    "remote_software_available": active,
                    "available": True,
                },
                captured_at=self._last_success_time,
            )
        except Exception as e:
            self._last_error = f"远程会话检测异常: {str(e)[:80]}"
            logger.debug(f"RemoteSessionSource 读取失败: {e}")
            return DataPoint(
                source=self.name,
                data={"remote_control_active": False, "available": False},
            )

    @property
    def connection_state(self) -> str:
        return "connected" if self._last_success_time else "disconnected"

    @property
    def last_error(self):
        return self._last_error
