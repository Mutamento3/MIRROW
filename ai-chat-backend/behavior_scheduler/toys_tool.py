# 情趣玩具蓝牙控制工具 — XXD 协议（已验证）
import asyncio
import logging
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from bleak import BleakClient
    from .ble_worker import run_ble
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False

# ─── 已验证的设备常量 ─────────────────────────────────────────
# MAC 可配置：env TOY1_MAC → settings silicon_perception.toys.toy1_mac → None（开源默认不填）
WRITE_UUID = "00000003-0000-1000-8000-00805f9b34fb"


def _get_toy1_mac() -> Optional[str]:
    """玩具1 MAC：env → settings → None。仿 heart_rate.py 降级范式。"""
    import os
    mac = os.getenv("TOY1_MAC")
    if mac and mac.strip():
        return mac.strip()
    try:
        from settings_manager import get_setting
        ss = get_setting("silicon_perception") or {}
        mac = (ss.get("toys", {}) or {}).get("toy1_mac")
        if mac and str(mac).strip():
            return str(mac).strip()
    except Exception:
        pass
    return None

# ─── XXD 9字节 XOR 加密 ───────────────────────────────────────

def _derive_key(mac: str) -> tuple:
    mac_bytes = [int(x, 16) for x in mac.replace("-", ":").split(":")]
    n = 0
    for b in mac_bytes:
        n ^= b
    i = 4 ^ n
    return (0x80 | (i >> 4), 0x80 | (i & 0x0F), i,
            (0x80 | (i >> 4)) ^ (0x80 | (i & 0x0F)) ^ i)


def _build_packet(key, logo, cat1, cat2, gear) -> bytes:
    data = bytearray(9)
    data[0] = key[0]; data[1] = key[1]; data[2] = key[2]; data[3] = key[3]
    data[4] = logo; data[5] = cat1; data[6] = cat2; data[7] = gear
    checksum = 0
    for j in range(8):
        checksum ^= data[j]
    data[8] = checksum
    return bytes(data)


class ToysTool(BaseTool):
    name = "toys"
    # Retired model-facing entry. Keep the executor for historical tool records
    # and explicit legacy calls, but do not compete with named TianYan presets.
    visible_to_pro = False
    ui_only_result = True
    single_use = True  # 玩具需用户反馈，一轮只能调一次
    description = (
        "通过蓝牙控制情趣玩具（XXD-Lush12N）。连接后保持在线，支持连续调节。"
        "操作：vibrate（1-19档）、stop（停止）、pulse（短脉冲）、battery（查询电量）、heat（加热）。"
    )
    flash_description = "蓝牙情趣玩具（XXD-Lush12N）——振动/停止/脉冲/电量/加热"

    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["vibrate", "stop", "pulse", "battery", "heat"],
                "description": "操作类型"
            },
            "intensity": {
                "type": "integer",
                "description": "振动强度 0-19。0=停止，1-19=强度",
                "default": 5
            },
            "heat_on": {
                "type": "boolean",
                "description": "加热开关（仅 action=heat）",
                "default": False
            }
        },
        "required": ["action"]
    }

    def __init__(self):
        self._client: Optional[BleakClient] = None
        self._key: Optional[tuple] = None

    def get_user_facing_description(self, **kwargs) -> str:
        action = kwargs.get("action", "")
        if action == "vibrate":
            return f"正在将玩具调到 {kwargs.get('intensity', 0)}/19 档"
        elif action == "stop":
            return "正在停止玩具"
        elif action == "pulse":
            return "正在发送短脉冲"
        elif action == "battery":
            return "正在查询电量"
        elif action == "heat":
            return "正在切换加热"
        return "正在控制玩具"

    async def execute(self, **kwargs) -> ToolResult:
        if not BLEAK_AVAILABLE:
            return ToolResult(status=ToolStatus.ERROR, content="", error="需要安装 bleak: pip install bleak")

        action = kwargs.get("action", "vibrate")
        intensity = int(kwargs.get("intensity", 5))
        mac = kwargs.get("device_address") or _get_toy1_mac()
        if not mac:
            return ToolResult(status=ToolStatus.ERROR, content="",
                              error="未配置玩具设备（请在后端 .env 设置 TOY1_MAC）")

        if not self._key:
            self._key = _derive_key(mac)

        try:
            if action == "battery":
                return await self._send(mac, 101, 61, 51, 1, "电量已查询")

            if action == "heat":
                gear = 1 if kwargs.get("heat_on", False) else 0
                return await self._send(mac, 101, 63, 53, gear,
                                        "加热已开启" if gear else "加热已关闭")

            if action == "stop":
                return await self._send(mac, 101, 58, 48, 0, "振动已停止")

            if action == "vibrate":
                gear = max(0, min(19, intensity))
                if gear == 0:
                    return await self._send(mac, 101, 58, 48, 0, "振动已停止")
                return await self._send(mac, 101, 58, 48, gear, f"振动 {gear}/19 档")

            if action == "pulse":
                gear = max(1, min(19, intensity))
                await self._ensure_connected(mac)
                pkt = _build_packet(self._key, 101, 58, 48, gear)
                await run_ble(self._client.write_gatt_char(WRITE_UUID, pkt, response=False))
                await asyncio.sleep(1.5)
                pkt_stop = _build_packet(self._key, 101, 58, 48, 0)
                await run_ble(self._client.write_gatt_char(WRITE_UUID, pkt_stop, response=False))
                return ToolResult(status=ToolStatus.SUCCESS,
                                  content=f"短脉冲 {gear}/19 档，已自动停止")

            return ToolResult(status=ToolStatus.ERROR, content="", error=f"未知操作: {action}")

        except Exception as e:
            logger.error(f"toys_tool error: {e}")
            await self._disconnect()
            return ToolResult(status=ToolStatus.ERROR, content="", error=str(e))

    # ─── 内部方法 ────────────────────────────────────────────

    async def _ensure_connected(self, mac: str):
        if self._client and self._client.is_connected:
            return
        self._client = BleakClient(mac, timeout=10.0)
        await run_ble(self._client.connect())
        if not self._key:
            self._key = _derive_key(mac)

    async def _disconnect(self):
        if self._client:
            try:
                await run_ble(self._client.disconnect())
            except Exception:
                pass
            self._client = None

    async def _send(self, mac, logo, cat1, cat2, gear, desc) -> ToolResult:
        """发送命令并保持连接（不再每次断开）"""
        await self._ensure_connected(mac)
        pkt = _build_packet(self._key, logo, cat1, cat2, gear)
        try:
            await run_ble(self._client.write_gatt_char(WRITE_UUID, pkt, response=True))
        except Exception:
            await run_ble(self._client.write_gatt_char(WRITE_UUID, pkt, response=False))
        # 通知哨兵：玩具已使用（供 M4 规则"玩具后心率恢复慢"检测）
        try:
            from silicon_perception.analysis.anomaly_detector import AnomalyDetector
            AnomalyDetector.mark_toy_used()
        except Exception:
            pass
        return ToolResult(status=ToolStatus.SUCCESS, content=desc)
