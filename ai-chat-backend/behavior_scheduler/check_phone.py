"""
📱 AI查手机 — AI 的设备感知层

检查点前缀:
  [PHONE:1] — execute() 入口/出口
  [PHONE:2] — 各路采集
  [PHONE:4] — 哨兵数据

管道: 并行采集(当前前台应用 + 电量 + 屏幕使用统计 + 可选截屏) → 分源格式化结果
哨兵屏幕统计从 HealthStore 读取；当前前台应用走手机后台 Relay 的结构化观测。
截屏只负责取得原图，由调用方共用视觉对话合同分析，不替代当前前台身份。

用法:
  查手机 → 并行采集真实设备事实与可选原图 → 调用方统一理解与回复
"""
import asyncio
import time as _time
from typing import Dict, Any, List, Optional

from .base_tool import BaseTool, ToolResult, ToolStatus
from context_scheduler_log import write_log as _log

_SOURCE_TIMEOUT = 15  # 手机 relay 超时


class CheckPhoneTool(BaseTool):
    name = "check_phone"
    description = (
        "查人类伙伴手机：获取当前前台应用与亮屏状态、电量、屏幕使用统计和可选截屏。"
        "前台应用是手机实时观测；可选截屏原图交给本轮统一视觉分析，两个来源分别标注。"
    )
    flash_description = "查人类伙伴手机——当前前台应用/亮屏状态 + 电量 + 屏幕使用统计 + 可选截屏"
    single_use = True

    parameters_schema = {
        "type": "object",
        "properties": {
            "include_screenshot": {
                "type": "boolean",
                "description": "是否包含截屏（默认true）",
                "default": True
            }
        },
        "required": []
    }

    def __init__(self, mobile_relay=None, camera_tool=None,
                 screenshot_tool=None, get_tools=None):
        self._mobile_relay = mobile_relay
        self._camera_tool = camera_tool        # 用于视觉分析（GLM pipeline）
        self._screenshot_tool = screenshot_tool
        self._get_tools = get_tools or (lambda: {})
        self._mobile_disabled = False

    def get_user_facing_description(self, **kwargs) -> str:
        return "正在查手机..."

    async def execute(self, **kwargs) -> ToolResult:
        t0 = _time.time()
        include_screenshot = kwargs.get("include_screenshot", True)
        self._mobile_disabled = False
        _log(f"[PHONE:1] ═══ execute 入口 | screenshot={include_screenshot} | relay={'SET' if self._mobile_relay else 'NONE'} ═══")

        # ── Relay 连通性检查 ──
        if self._mobile_relay:
            _log("[PHONE:1] 🔍 Relay ping (mobile_battery)...")
            try:
                import uuid
                ping = await asyncio.wait_for(
                    self._mobile_relay(str(uuid.uuid4()), "mobile_battery", {}),
                    timeout=15.0
                )
                _log(f"[PHONE:1] ✅ Relay 通: {str(ping)[:100]}")
            except asyncio.TimeoutError:
                _log("[PHONE:1] ❌ Relay ping 超时 — 手机WS断开！")
                self._mobile_disabled = True
            except Exception as e:
                _log(f"[PHONE:1] ❌ Relay ping 异常: {e} — 跳过手机源")
                self._mobile_disabled = True

        if self._mobile_disabled:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="人类伙伴手机端没有运行 MIRROW，无法查询手机状态。",
                error="手机未连接"
            )

        # ── 并行采集：电量 + 屏幕时间 + 前台应用 + 截屏 + 哨兵数据 ──
        tasks = {}
        _log("[PHONE:2] 开始并行采集...")

        # 电量
        tasks["battery"] = asyncio.create_task(
            self._relay("mobile_battery", {}), name="battery"
        )
        # 屏幕时间（mobile relay）
        tasks["usage"] = asyncio.create_task(
            self._relay("mobile_usage", {}), name="usage"
        )
        tasks["current_app"] = asyncio.create_task(
            self._relay("mobile_current_app", {}), name="current_app"
        )
        # 最近通知（附带信息——查手机时顺带看通知摘要，不做成 AI 单独主动调用的工具）
        tasks["notifications"] = asyncio.create_task(
            self._relay("mobile_notifications", {}), name="notifications"
        )
        # 截屏（可选）
        if include_screenshot:
            tasks["screenshot"] = asyncio.create_task(
                self._relay_screenshot(), name="screenshot"
            )
        # 哨兵手机数据（本地 HealthStore，无需手机连接）
        tasks["sentinel"] = asyncio.create_task(
            self._get_sentinel_phone_data(), name="sentinel"
        )

        # 等待所有任务
        results = {}
        for key, task in tasks.items():
            try:
                results[key] = await task
            except Exception as e:
                _log(f"[PHONE:2] ❌ {key} 异常: {e}")
                results[key] = {"success": False, "error": str(e)}

        dt = _time.time() - t0
        _log(f"[PHONE:2] 采集完成 ({dt:.1f}s) | battery={results.get('battery',{}).get('success')} usage={results.get('usage',{}).get('success')} current_app={results.get('current_app',{}).get('success')} screenshot={results.get('screenshot',{}).get('success')} notifs={results.get('notifications',{}).get('success')} notifs_data={len(results.get('notifications',{}).get('data') or [])}")

        # ── 格式化结果 ──
        lines = ["📱 **人类伙伴手机状态**\n"]

        # 电量
        batt = results.get("battery", {})
        if batt.get("success") and batt.get("data"):
            bd = batt["data"]
            level = bd.get("level", "?")
            status = bd.get("status", "?")
            status_cn = {"charging": "🔌 充电中", "discharging": "🔋 放电中",
                         "full": "✅ 已充满", "usb": "🔌 USB充电",
                         "wireless": "🔄 无线充电"}.get(status, status)
            temp = f" ({bd['temperature']}°C)" if "temperature" in bd else ""
            lines.append(f"**电量**: {level}% | {status_cn}{temp}")
        else:
            lines.append(f"**电量**: 无法获取 — {batt.get('content', batt.get('error', '未知错误'))}")

        # 屏幕时间（Java 返回字段: total_minutes, top_apps[{package, minutes}]）
        usage = results.get("usage", {})
        if usage.get("success") and usage.get("data"):
            ud = usage["data"]
            screen_time = ud.get("total_minutes", 0)
            hours = screen_time // 60
            mins = screen_time % 60
            time_str = f"{hours}小时{mins}分钟" if hours > 0 else f"{mins}分钟"
            lines.append(f"**屏幕时间**: {time_str}")
            top_apps = ud.get("top_apps", [])
            if top_apps:
                apps_str = "、".join(
                    f"{a.get('package', a.get('name', '?'))}({a.get('minutes', a.get('duration_minutes', 0))}min)"
                    for a in top_apps[:5]
                )
                lines.append(f"**常用App**: {apps_str}")
        else:
            lines.append(f"**屏幕时间**: 无法获取 — {usage.get('content', usage.get('error', '未知错误'))}")

        # 前台身份与画面分开陈述。设备的观测时间过旧时，不把旧应用冒充当前。
        current_app = results.get("current_app", {})
        if current_app.get("success") and isinstance(current_app.get("data"), dict):
            app_data = current_app["data"]
            observed_ms = app_data.get("observed_at_ms")
            try:
                age_seconds = max(0, _time.time() - float(observed_ms) / 1000) if observed_ms else None
            except (TypeError, ValueError, OverflowError):
                age_seconds = None
            if app_data.get("available") is False:
                lines.append("**当前手机活动**: 前台观测不可用")
            elif age_seconds is None or age_seconds > 300:
                lines.append("**当前手机活动**: 前台观测不可用或已过期")
            elif app_data.get("screen_on") is False:
                lines.append("**当前手机活动**: 屏幕已熄灭")
            elif app_data.get("screen_on") is not True:
                lines.append("**当前手机活动**: 前台观测不可用或已过期")
            else:
                from silicon_perception.collection.app_name_map import is_filtered_package, resolve_app_name
                package = str(app_data.get("package") or "").strip()
                app_name = (resolve_app_name(package) or app_data.get("app_name") or package)
                if package and not is_filtered_package(package) and app_data.get("foreground_available", True):
                    age_text = f"（约{int(age_seconds)}秒前观测）"
                    lines.append(f"**手机前台应用**: {app_name}{age_text}；屏幕亮着（仅代表设备状态）")
                else:
                    lines.append("**当前手机活动**: 屏幕亮着，内容应用未知")
        else:
            lines.append("**当前手机活动**: 前台应用暂不可用")

        # 哨兵屏幕统计（本地缓存，即使手机断连也有效）
        sentinel_data = results.get("sentinel", {})
        if sentinel_data.get("screen_time_minutes") is not None:
            st = sentinel_data["screen_time_minutes"]
            h, m = int(st) // 60, int(st) % 60
            lines.append(f"**手机屏幕时间**（哨兵）: {h}小时{m}分钟")
        if sentinel_data.get("top_app_category"):
            cat = sentinel_data["top_app_category"]
            cat_cn = {"social": "社交", "entertainment": "娱乐", "game": "游戏",
                      "browser": "浏览器", "work": "工作", "mirrow": "MIRROW"}.get(cat, cat)
            lines.append(f"**主要App类别**（哨兵）: {cat_cn}")

        activity_lines = list(lines)
        # 截屏分析
        screenshot = results.get("screenshot", {})
        if screenshot.get("success"):
            lines.append("\n**屏幕内容**: 已取得手机截屏，原图随本次工具结果提供，尚待统一视觉分析。")
        elif screenshot.get("success") is False:
            lines.append(f"\n**屏幕内容**: 截屏失败 — {screenshot.get('error', '未知')}")

        notifications_start = len(lines)
        # 当前通知查询保留真实发布时间与来源；旧缓存不冒充正在通话。
        notifs = results.get("notifications", {})
        if notifs.get("success"):
            from silicon_perception.collection.notifications import (
                format_notification_snapshot, normalize_notifications,
            )
            lines.append(format_notification_snapshot(notifs))
            # The diagnostic extra_data follows the same time boundary as visible text.
            notifs["data"] = normalize_notifications(notifs)
        elif notifs:
            lines.append("\n**通知栏近期通知**：本次查询不可用，当前通知与通话状态未知。")

        content = "\n".join(lines)
        _log(f"[PHONE:1] ═══ execute 完成 ({_time.time()-t0:.1f}s) | 结果 {len(content)} chars ═══")

        image = screenshot.pop("image_url", None)
        if include_screenshot:
            screenshot['captured'] = bool(image)
        extra_data = {"sources": results,
                      "phone_activity_text": "\n".join(activity_lines + lines[notifications_start:])}
        if image:
            extra_data.update({
                "images": [{"source_key": "phone_screen", "label": "手机屏幕", "image_url": image}],
                "vision_analyzed": False,
            })
        return ToolResult(
            status=ToolStatus.SUCCESS,
            content=content,
            extra_data=extra_data
        )

    async def _get_sentinel_phone_data(self) -> Dict:
        """从哨兵 HealthStore 读取手机屏幕数据（本地缓存，无需 relay）。"""
        try:
            from silicon_perception.recording.health_store import get_store
            store = get_store()
            snaps = store.get_recent_snapshots(lookback_seconds=600)  # 最近10分钟
            result = {}
            for row in snaps:
                st = row.get("screen_time_minutes")
                if st is not None and "screen_time_minutes" not in result:
                    result["screen_time_minutes"] = st
                cat = row.get("top_app_category")
                if cat and "top_app_category" not in result:
                    result["top_app_category"] = cat
                if "screen_time_minutes" in result and "top_app_category" in result:
                    break
            _log(f"[PHONE:4] 哨兵数据: {result}")
            return result
        except Exception as e:
            _log(f"[PHONE:4] 哨兵数据读取失败: {e}")
            return {}

    async def _relay(self, tool_name: str, params: dict) -> Dict:
        """手机中继调用（非截屏）"""
        if not self._mobile_relay or self._mobile_disabled:
            return {"success": False, "error": "手机未连接"}
        try:
            import uuid
            result = await asyncio.wait_for(
                self._mobile_relay(str(uuid.uuid4()), tool_name, params),
                timeout=_SOURCE_TIMEOUT
            )
            if isinstance(result, dict):
                return {
                    "success": result.get("success", False),
                    "content": result.get("content", ""),
                    "data": result.get("data"),
                    "error": result.get("error"),
                    **({key: result[key] for key in ("snapshot_scope", "observed_at_ms")
                        if key in result} if tool_name == "mobile_notifications" else {}),
                }
            return {"success": False, "error": f"异常返回类型: {type(result).__name__}"}
        except asyncio.TimeoutError:
            return {"success": False, "error": f"{tool_name} 超时"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _relay_screenshot(self) -> Dict:
        """Capture only; conversational vision is owned by the caller."""
        if not self._mobile_relay or self._mobile_disabled:
            return {"success": False, "error": "手机未连接"}
        try:
            import uuid
            _log("[PHONE:2] 📸 截屏 relay 调用...")
            t0 = _time.time()
            result = await asyncio.wait_for(
                self._mobile_relay(str(uuid.uuid4()), "mobile_screenshot", {}),
                timeout=_SOURCE_TIMEOUT
            )
            dt = _time.time() - t0
            _log(f"[PHONE:2] 📸 截屏 relay 返回 ({dt:.1f}s)")

            if not isinstance(result, dict):
                return {"success": False, "error": "异常返回类型"}
            if not result.get("success"):
                return {"success": False, "error": result.get("content", "截屏失败")}

            extra = result.get("extra_data") or {}
            image_b64 = extra.get("image_base64") or result.get("image_base64")
            if not image_b64:
                return {"success": False, "error": "截屏未返回图片"}

            return {"success": True, "image_url": f"data:image/jpeg;base64,{image_b64}"}

        except asyncio.TimeoutError:
            return {"success": False, "error": "截屏超时"}
        except Exception as e:
            return {"success": False, "error": f"截屏异常: {e}"}
