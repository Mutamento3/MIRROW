"""数据源抽象基类 — DataSource ABC + DataPoint 数据模型"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


# One inventory for monitoring and reviews; this is descriptive metadata, not a
# second decision engine.  Freshness remains enforced by the existing snapshot/
# builder consumers because different facts have different safety requirements.
SOURCE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "heart_rate": {"kind": "point_measurement", "time_basis": "device_measurement", "nominal_max_age_seconds": 900, "proves": "一次心率读数", "does_not_prove": "持续心率或情绪"},
    "screen_activity": {"kind": "current_state", "time_basis": "read_time", "nominal_max_age_seconds": 120, "proves": "PC画面活动与前台窗口", "does_not_prove": "人类伙伴本人正在操作"},
    "input_idle": {"kind": "event_age", "time_basis": "raw_input_event", "nominal_max_age_seconds": 180, "proves": "已分类的本地或远程键鼠事件", "does_not_prove": "来源未知输入属于人类伙伴"},
    "mirrow_visibility": {"kind": "liveness", "time_basis": "frontend_heartbeat", "nominal_max_age_seconds": 15, "proves": "MIRROW页面近期可见", "does_not_prove": "人类伙伴正在阅读"},
    "user_status": {"kind": "user_declaration", "time_basis": "valid_until_changed", "nominal_max_age_seconds": None, "proves": "人类伙伴最近一次状态声明", "does_not_prove": "设备观测与声明完全一致"},
    "period": {"kind": "calendar_fact", "time_basis": "logical_day", "nominal_max_age_seconds": 86400, "proves": "记录中的生理期日序", "does_not_prove": "实时身体感受"},
    "step_count": {"kind": "cumulative_window", "time_basis": "device_measurement", "nominal_max_age_seconds": 900, "proves": "05:00起算的设备步数", "does_not_prove": "断连期间静止"},
    "wifi_state": {"kind": "current_state", "time_basis": "device_observation", "nominal_max_age_seconds": 300, "proves": "网络连接及家庭网络匹配", "does_not_prove": "精确室内位置"},
    "gps_location": {"kind": "current_state", "time_basis": "device_measurement", "nominal_max_age_seconds": 1800, "proves": "坐标与地址解析结果", "does_not_prove": "持续停留或具体活动"},
    "screen_time": {"kind": "cumulative_window", "time_basis": "local_05_00_logical_day", "nominal_max_age_seconds": 3600, "proves": "各App前台累计时长", "does_not_prove": "当前正在使用某App"},
    "current_app": {"kind": "current_state", "time_basis": "device_window_observation", "nominal_max_age_seconds": 300, "proves": "亮屏状态与前台内容App身份", "does_not_prove": "屏幕内容类别或人类伙伴注意力"},
    "mobile_vision": {"kind": "inferred_content", "time_basis": "screenshot_capture", "nominal_max_age_seconds": 300, "proves": "同包名截图的低层内容描述", "does_not_prove": "App身份或未确认的游戏行为"},
    "notifications": {"kind": "snapshot_window", "time_basis": "original_post_time_and_snapshot_observation", "nominal_max_age_seconds": 120, "proves": "当前通知栏中的近期通知来源与数量", "does_not_prove": "人类伙伴已阅读、回应或正在通话"},
    "running_processes": {"kind": "capability_snapshot", "time_basis": "read_time", "nominal_max_age_seconds": 600, "proves": "PC进程正在运行", "does_not_prove": "进程位于前台或人类伙伴正在使用"},
    "remote_session": {"kind": "capability_snapshot", "time_basis": "read_time", "nominal_max_age_seconds": 600, "proves": "远控软件进程存在", "does_not_prove": "当前有远程控制行为"},
    "sensor_context": {"kind": "multi_slot_current_state", "time_basis": "oldest_required_slot_observation", "nominal_max_age_seconds": 600, "proves": "手机姿态、运动、光线和屏幕传感器状态", "does_not_prove": "人类伙伴姿势或注意力"},
    "weather": {"kind": "forecast_snapshot", "time_basis": "provider_observation", "nominal_max_age_seconds": 21600, "proves": "配置城市天气", "does_not_prove": "人类伙伴所在微环境"},
}


def _parse_hr(data: bytes) -> int:
    """解析标准 BLE Heart Rate Measurement 数据包。
    Flags byte bit 0: 0=8-bit HR, 1=16-bit HR (little-endian)."""
    flags = data[0]
    if flags & 0x01:
        return int.from_bytes(data[1:3], "little")
    return data[1]


@dataclass
class DataPoint:
    """统一数据点 — 所有数据源产出的标准格式"""
    source: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    data: Dict[str, Any] = field(default_factory=dict)
    # 数据真实有效时间（采集成功时刻，非构造时刻）。None = 本次无新数据（显式不可用标记）。
    # 与 timestamp 区别：timestamp 是"读取尝试"时刻，captured_at 是"数据有效"时刻。
    captured_at: Optional[str] = None


class DataSource(ABC):
    """数据源抽象基类 — 所有数据源继承此类"""
    name: str = "base"

    def contract(self) -> Dict[str, Any]:
        """Return read-only observation semantics for diagnostics/UI."""
        return dict(SOURCE_CONTRACTS.get(self.name, {
            "kind": "unspecified",
            "time_basis": "unspecified",
            "nominal_max_age_seconds": None,
            "proves": "",
            "does_not_prove": "",
        }))

    @abstractmethod
    async def read(self) -> DataPoint:
        """读取一次数据，失败时返回空 DataPoint（不抛异常）"""
        ...

    async def start(self):
        """启动数据源（如建立持久连接），默认无操作"""
        pass

    async def stop(self):
        """停止数据源（如断开连接），默认无操作"""
        pass
