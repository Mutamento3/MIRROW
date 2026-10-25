# 情趣玩具蓝牙控制工具 — 甜焰 / TianYan 55 协议（iOS+buttplug 验证）
import asyncio
import logging
import uuid
from .base_tool import BaseTool, ToolResult, ToolStatus
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from bleak import BleakClient
    from .ble_worker import run_ble
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False

# 设备可配置：env TOY2_MAC/TOY2_NAME → settings silicon_perception.toys.toy2_* → 默认
WRITE_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _get_toy2_mac() -> Optional[str]:
    """玩具2 MAC：env → settings → None。"""
    import os
    mac = os.getenv("TOY2_MAC")
    if mac and mac.strip():
        return mac.strip()
    try:
        from settings_manager import get_setting
        ss = get_setting("silicon_perception") or {}
        mac = (ss.get("toys", {}) or {}).get("toy2_mac")
        if mac and str(mac).strip():
            return str(mac).strip()
    except Exception:
        pass
    return None


def _get_toy2_name() -> str:
    """玩具2 BLE 名称过滤：env → settings → 默认 TianYan。"""
    import os
    name = os.getenv("TOY2_NAME")
    if name and name.strip():
        return name.strip()
    try:
        from settings_manager import get_setting
        ss = get_setting("silicon_perception") or {}
        name = (ss.get("toys", {}) or {}).get("toy2_name")
        if name and str(name).strip():
            return str(name).strip()
    except Exception:
        pass
    return "TianYan"

# ─── TianYan 55 协议 6 字节 ──────────────────────────────────
# 55 <cmd> <p2> <p3> <mode> <flag>
CMD_MAP = {
    "stretch": (0x08, 0x0A),   # 伸缩
    "suck":    (0x09, 0x00),   # 吮吸
    "vibrate": (0x03, 0x00),   # 振动 — 55 03 00 <kind> <preset> <intensity>（buttplug 验证）
}
MAX_MODES = {"stretch": 8, "suck": 8}

# ─── 预设模式（v3 — 振动/伸缩/吮吸 三维度渐进）───────────
PRESETS = {
    # ═══ 前戏 — 振动挑逗 ═══
    "轻吟": {
        "action": "vibrate", "intensity": 3,
        "heat": False,
        "feel": "酥麻低鸣，若即若离的试探"
    },
    "激奏": {
        "action": "vibrate", "intensity": 8,
        "heat": False,
        "feel": "霸道强势的节拍，逼近感十足"
    },

    # ═══ 渐进 — 伸缩/吮吸 ═══
    "微澜": {
        "action": "stretch", "mode": 4, "intensity": 2,
        "heat": False,
        "feel": "温和波浪——身体开始回应"
    },
    "潮起": {
        "action": "stretch+suck", "mode": 3, "intensity": 2,
        "heat": False,
        "feel": "伸缩+吮吸内外夹击，潮水初涨"
    },
    "深吮": {
        "action": "suck", "mode": 5, "intensity": 3,
        "heat": False,
        "feel": "纯吮吸，专注而猛烈"
    },

    # ═══ 冲刺 — 全开组合 ═══
    "酥麻": {
        "action": "stretch+vibrate", "mode": 4, "intensity": 2,
        "vibrate_intensity": 7,
        "heat": False,
        "feel": "推进中震颤，双重叠加"
    },
    "炽焰": {
        "action": "stretch+suck", "mode": 6, "intensity": 3,
        "heat": True,
        "feel": "加热双开——炽热火焰蔓延全身 🔥"
    },
    "巅峰": {
        "action": "stretch+suck+vibrate", "mode": 8, "intensity": 3,
        "vibrate_intensity": 10,
        "heat": True,
        "feel": "全开极限——伸缩+吮吸+振动+加热，不留退路 🔥"
    },

    # ═══ 停止 ═══
    "停歇": {
        "action": "stop", "mode": 0, "intensity": 0,
        "heat": False,
        "feel": "全部停止——给她喘息的间隙"
    },
}


class Toy2Tool(BaseTool):
    name = "toy2"
    ui_only_result = True
    single_use = True  # 玩具需用户反馈，一轮只能调一次
    description = (
        "甜焰——比跳蛋更烈的情趣玩具。"
        "只使用命名模式: 轻吟→激奏→微澜→潮起→深吮→酥麻→炽焰→巅峰 | 停歇=全停。"
        "前戏振动→渐进伸缩/吮吸→冲刺全开。高强度档(炽焰/巅峰)自带加热🔥。"
    )
    pro_guide = (
        "甜焰实际硬件控制：前戏振动轻吟、激奏；渐进伸缩/吮吸微澜、潮起、深吮；"
        "冲刺酥麻、炽焰、巅峰，炽焰和巅峰自带加热；停歇为全停。"
        "可加强、减弱、切换、停止、重启或保持，动作中说明选择的命名模式，不写底层参数。"
        "本轮❤️开关见当前能力状态；只有工具成功回执才代表硬件真实执行。"
    )
    flash_description = "甜焰情趣玩具——从命名模式中选择一个；停歇会立即停止"
    parameters_schema = {
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "enum": list(PRESETS.keys()),
                "description": (
                    "模式名。前戏: 轻吟→激奏。渐进: 微澜→潮起→深吮。冲刺: 酥麻→炽焰→巅峰。"
                    "炽焰/巅峰自带加热；停歇=全停。"
                )
            }
        },
        "required": ["preset"],
        "additionalProperties": False,
    }

    def __init__(self):
        self._client = None
        self._mac = None
        self._vibe_task = None

    def _cancel_vibe(self):
        if self._vibe_task and not self._vibe_task.done():
            self._vibe_task.cancel()
        self._vibe_task = None

    def get_user_facing_description(self, **kwargs) -> str:
        preset = kwargs.get("preset", "")
        action = kwargs.get("action", "")
        mode = kwargs.get("mode", 1)

        if preset and preset in PRESETS:
            p = PRESETS[preset]
            if preset == "停歇":
                return "甜焰安静下来，给她一点喘息的时间"
            heat_mark = "🔥" if p.get("heat") else ""
            return f"甜焰「{preset}」{heat_mark}——{p['feel'][:24]}"

        if action == "stop": return "甜焰……先休息一下"
        if action == "stretch": return f"甜焰正在温柔推进（{mode}/8）"
        if action == "suck": return f"甜焰狠狠吸住了（{mode}/8）"
        if action == "heat": return "甜焰正在预热……"
        if action == "combo": return f"甜焰伸缩+吮吸双重刺激（{mode}/8）"
        if action == "vibrate":
            vi = int(kwargs.get("intensity", 5))
            return f"甜焰嗡嗡震动中（强度{vi}/10）"
        return "甜焰已就绪"

    # ═══════════════════════════════════════════════
    # 主入口：预设解析 → 路由决策 → BLE 执行
    # ═══════════════════════════════════════════════

    async def execute(self, **kwargs) -> ToolResult:
        # ── 1. 预设解析 ──
        preset_name = kwargs.get("preset")
        if preset_name and preset_name in PRESETS:
            p = PRESETS[preset_name]
            kwargs["action"] = p["action"]
            kwargs["mode"] = p.get("mode", 1)
            kwargs["intensity"] = p.get("intensity", 0)
            kwargs["_heat"] = p.get("heat", False)
            kwargs["_vibrate_intensity"] = p.get("vibrate_intensity", 0)
        else:
            kwargs["_heat"] = kwargs.get("_heat", False)
            kwargs["_vibrate_intensity"] = kwargs.get("_vibrate_intensity", 0)

        action = kwargs.get("action")
        if not action:
            return ToolResult(
                status=ToolStatus.ERROR, content="",
                error="请选择甜焰命名模式: " + "|".join(PRESETS.keys())
            )

        # ── 2. 路由决策：手机优先 → 等待重连 → PC 回退 ──
        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                # 手机 relay 暂时不可用（wander WS 可能刚断开正在重连），等 3s 再试
                logger.info(f"toy2 路由: 手机 relay 暂不可用，等待重连(3s)...")
                await asyncio.sleep(3)
                relay = get_mobile_relay_callback()
            if relay:
                logger.info(f"toy2 路由: 手机 BLE (preset={preset_name}, action={action})")
                return await self._execute_via_phone(relay, **kwargs)
        except Exception as e:
            logger.debug(f"toy2 手机路由检查跳过: {e}")

        logger.info(f"toy2 路由: PC BLE (preset={preset_name}, action={action})")
        return await self._execute_local_ble(**kwargs)

    # ═══════════════════════════════════════════════
    # 手机 BLE 执行
    # ═══════════════════════════════════════════════

    async def _execute_via_phone(self, relay, **kwargs) -> ToolResult:
        action = kwargs.get("action", "stretch")
        mode = int(kwargs.get("mode", 1))
        intensity = int(kwargs.get("intensity", 0) or 0)
        heat = kwargs.get("_heat", False)
        vibrate_intensity = kwargs.get("_vibrate_intensity", 0)

        params = {
            "action": action,
            "mode": mode,
            "intensity": intensity,
            "heat": heat,
            "vibrate_intensity": vibrate_intensity,
            "mac": _get_toy2_mac(),
        }

        try:
            request_id = f"toy2_{uuid.uuid4().hex[:8]}"
            result = await asyncio.wait_for(
                relay(request_id, "mobile_toy2", params),
                timeout=30.0
            )
            if isinstance(result, dict):
                success = result.get("success", False)
                content = result.get("content", "")
                error = result.get("error", "")
            else:
                success = "ok" in str(result).lower() or "成功" in str(result)
                content = str(result)
                error = "" if success else str(result)

            try:
                from silicon_perception.analysis.anomaly_detector import AnomalyDetector
                AnomalyDetector.mark_toy_used()
            except Exception:
                pass

            if success:
                return ToolResult(status=ToolStatus.SUCCESS, content=content)
            else:
                logger.warning(f"toy2 手机执行失败: {error or content}")
                return ToolResult(status=ToolStatus.ERROR, content="", error=error or content)
        except asyncio.TimeoutError:
            logger.error("toy2 手机执行超时 (30s)")
            return ToolResult(status=ToolStatus.ERROR, content="", error="手机 BLE 超时（30s）")
        except Exception as e:
            logger.error(f"toy2 手机执行异常: {e}")
            return ToolResult(status=ToolStatus.ERROR, content="", error=f"手机 relay 异常: {e}")

    # ═══════════════════════════════════════════════
    # PC 本地 BLE 执行
    # ═══════════════════════════════════════════════

    async def _execute_local_ble(self, **kwargs) -> ToolResult:
        if not BLEAK_AVAILABLE:
            return ToolResult(status=ToolStatus.ERROR, content="", error="需要安装 bleak: pip install bleak")

        action = kwargs.get("action", "stretch")
        mode = int(kwargs.get("mode", 1))
        intensity = int(kwargs.get("intensity", 0) or 0)
        heat = kwargs.get("_heat", False)
        vibrate_intensity = kwargs.get("_vibrate_intensity", 0)
        INTENSITY_MAP = {1: 0x40, 2: 0xA0, 3: 0xFF}
        p3 = INTENSITY_MAP.get(intensity, 0)

        try:
            await self._ensure_connected()

            # TianYan keeps channels running independently. Match the phone's
            # stop-then-preset transition so a new mode cannot inherit motors
            # or heat from the previous one. Any failed stop aborts the switch.
            self._cancel_vibe()
            for cmd, flag in [(0x00, 0x00), (0x03, 0x00), (0x08, 0x0A),
                              (0x09, 0x00), (0x05, 0x00)]:
                await self._send(cmd, 0, 0, 0, flag)
                await asyncio.sleep(0.1)

            # ── heat 预热 ──
            if heat and action != "stop":
                await self._send_heat()
                await asyncio.sleep(0.6)

            # ── stop ──
            if action == "stop":
                return ToolResult(status=ToolStatus.SUCCESS, content="甜焰已安静下来")

            # ── heat ──
            if action == "heat":
                self._cancel_vibe()
                await self._send_heat()
                return ToolResult(status=ToolStatus.SUCCESS, content="甜焰暖了一下")

            # ── vibrate ──
            if action == "vibrate":
                self._cancel_vibe()
                vi = vibrate_intensity or intensity or 5
                vi = max(1, min(10, vi))
                await self._send_vibrate_raw(vi)
                try:
                    from silicon_perception.analysis.anomaly_detector import AnomalyDetector
                    AnomalyDetector.mark_toy_used()
                except Exception:
                    pass
                return ToolResult(status=ToolStatus.SUCCESS, content=f"甜焰振动 强度{vi}/10")

            # ── stretch+suck ──
            if action == "stretch+suck":
                self._cancel_vibe()
                p3_val = p3 or 0xFF
                m = max(1, min(8, mode))
                await self._send(0x08, 0, p3_val, m, 0x0A)
                await asyncio.sleep(0.1)
                await self._send(0x09, 0, p3_val, m, 0x00)
                heat_note = " +加热" if heat else ""
                return ToolResult(status=ToolStatus.SUCCESS,
                                  content=f"甜焰伸缩{m}+吮吸{m}双开（强度{intensity}/3）{heat_note}")

            # ── stretch+vibrate ──
            if action == "stretch+vibrate":
                self._cancel_vibe()
                p3_val = p3 or 0
                m = max(1, min(8, mode))
                await self._send(0x08, 0, p3_val, m, 0x0A)
                await asyncio.sleep(0.1)
                vi = vibrate_intensity or 5
                vi = max(1, min(10, vi))
                await self._send_vibrate_raw(vi)
                try:
                    from silicon_perception.analysis.anomaly_detector import AnomalyDetector
                    AnomalyDetector.mark_toy_used()
                except Exception:
                    pass
                return ToolResult(status=ToolStatus.SUCCESS,
                                  content=f"甜焰伸缩{m}+振动{vi}/10 双重叠加")

            # ── stretch+suck+vibrate 全开（巅峰） ──
            if action == "stretch+suck+vibrate":
                self._cancel_vibe()
                p3_val = p3 or 0xFF
                m = max(1, min(8, mode))
                await self._send(0x08, 0, p3_val, m, 0x0A)
                await asyncio.sleep(0.1)
                await self._send(0x09, 0, p3_val, m, 0x00)
                await asyncio.sleep(0.1)
                vi = vibrate_intensity or 10
                vi = max(1, min(10, vi))
                await self._send_vibrate_raw(vi)
                try:
                    from silicon_perception.analysis.anomaly_detector import AnomalyDetector
                    AnomalyDetector.mark_toy_used()
                except Exception:
                    pass
                return ToolResult(status=ToolStatus.SUCCESS,
                                  content=f"甜焰全开: 伸缩{m}+吮吸{m}+振动{vi} 🔥")

            # ── 旧 combo 兼容 ──
            if action == "combo":
                self._cancel_vibe()
                p3_val = p3 or 0xFF
                m = max(1, min(8, mode))
                await self._send(0x08, 0, p3_val, m, 0x0A)
                await asyncio.sleep(0.1)
                await self._send(0x09, 0, p3_val, m, 0x00)
                heat_note = " +加热" if heat else ""
                return ToolResult(status=ToolStatus.SUCCESS,
                                  content=f"甜焰伸缩{m}+吮吸{m}双开（强度{intensity}/3）{heat_note}")

            # ── stretch / suck 单发 ──
            cmd, flag = CMD_MAP.get(action, (0x08, 0x0A))
            if action in MAX_MODES:
                mode = max(1, min(MAX_MODES[action], mode))
            self._cancel_vibe()
            p3_val = p3 or 0
            await self._send(cmd, 0, p3_val, mode, flag)
            try:
                from silicon_perception.analysis.anomaly_detector import AnomalyDetector
                AnomalyDetector.mark_toy_used()
            except Exception:
                pass

            heat_note = " +加热" if heat else ""
            return ToolResult(status=ToolStatus.SUCCESS,
                              content=f"甜焰 {action} 模式 {mode}{heat_note}")

        except Exception as e:
            _err_str = str(e)
            # 将 Bleak 蓝牙错误转为用户友好消息
            if "No Bluetooth adapter found" in _err_str or "BleakBluetoothNotAvailable" in _err_str:
                _err_str = "PC 蓝牙不可用，请确认手机已连接 MIRROW app（甜焰应走手机 BLE 而非 PC）"
            logger.error(f"toy2 error: {e}")
            await self._disconnect()
            return ToolResult(status=ToolStatus.ERROR, content="", error=_err_str)

    # ═══════════════════════════════════════════════
    # 底层 BLE 发送
    # ═══════════════════════════════════════════════

    async def _send(self, cmd, p2, p3, p4, p5):
        pkt = bytes([0x55, cmd, p2, p3, p4, p5])
        await run_ble(self._client.write_gatt_char(WRITE_UUID, pkt, response=False))

    async def _send_heat(self):
        """加热 ON。0x85 是设备对 0x05 的 ACK，不是可下发命令。"""
        await self._send(0x05, 0x01, 0x37, 0x00, 0x00)

    async def _send_vibrate_raw(self, intensity):
        """振动 — buttplug 验证: 55 03 00 00 01 <intensity 1-10>"""
        vi = max(1, min(10, intensity)) if intensity > 0 else 0
        await self._send(0x03, 0x00, 0x00, 0x01, vi)

    async def _ensure_connected(self):
        if self._client and self._client.is_connected:
            return
        self._mac = self._mac or _get_toy2_mac()
        if not self._mac:
            raise RuntimeError("未配置玩具设备（请在后端 .env 设置 TOY2_MAC）")
        self._client = BleakClient(self._mac, timeout=15.0)
        await run_ble(self._client.connect())

    async def _disconnect(self):
        self._cancel_vibe()
        if self._client:
            try: await run_ble(self._client.disconnect())
            except Exception: pass
            self._client = None
