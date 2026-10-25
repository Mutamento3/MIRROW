"""手机视觉数据源 — 周期性截屏→轻量视觉分类→丰富 behavior_state

每 3 tick（≈3 分钟）检查手机亮屏状态，若亮屏则通过 relay 截屏，
用 GLM-4V 做轻量内容分类（非完整场景分析），结果写入 DataSnapshot。
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from .base import DataSource, DataPoint

logger = logging.getLogger(__name__)

# 每日截屏上限（3min × 10h = 200 次，免费模型 OK）
MAX_DAILY_CAPTURES = 200

# 视觉分类 prompt（轻量——只做内容归类，不做完整场景分析）
VISION_CLASSIFY_PROMPT = """Analyze this phone screenshot briefly. Return ONLY valid JSON:
{"app_name":"app name in Chinese if recognizable","content_category":"social|entertainment|game|work|shopping|reading|video|chat|system|unknown","activity_type":"browsing|watching|chatting|gaming|working|idle|unknown","description":"short Chinese description of what the user is doing, e.g. 在刷小红书穿搭帖子","confidence":0.0-1.0}

Keep description under 30 Chinese characters. Focus on content category, not pixel details."""


def _extract_image_base64(result: dict) -> Optional[str]:
    """Accept both native relay (top-level) and WebView relay (nested data) envelopes."""
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    nested = data if isinstance(data, dict) else {}
    for container in (nested, result):
        value = container.get("image_base64") or container.get("image") or container.get("base64")
        if value:
            return value
    return None


class MobileVisionSource(DataSource):
    """手机周期性截屏 → 轻量视觉分类"""

    name = "mobile_vision"
    tick_interval = 3  # 每 3 个基础 tick（≈3 分钟）

    def __init__(self):
        self._last_app_name: Optional[str] = None
        self._last_category: Optional[str] = None
        self._last_activity: Optional[str] = None
        self._last_description: Optional[str] = None
        self._last_confidence: Optional[float] = None
        self._last_success_time: Optional[str] = None
        self._consecutive_failures: int = 0
        self._today_captures: int = 0
        self._today_date: str = ""
        self._call_vision = None  # lazy init
        self._last_reason: Optional[str] = None
        self._last_error: Optional[str] = None

    def _unavailable(self, reason: str, *, expected: bool = False, include_cache: bool = False) -> DataPoint:
        """Build an explicit unavailable result without presenting cache as new evidence."""
        self._last_reason = reason
        if expected:
            self._consecutive_failures = 0
            self._last_error = None
        else:
            self._consecutive_failures += 1
            self._last_error = reason
        data = {"available": False, "reason": reason}
        if include_cache:
            data.update({
                "app_name": self._last_app_name,
                "category": self._last_category,
                "activity_type": self._last_activity,
                "description": self._last_description,
                "confidence": self._last_confidence,
            })
        return DataPoint(source=self.name, data=data, captured_at=self._last_success_time)

    async def _get_vision_func(self):
        """延迟获取视觉分析函数（避免循环导入）"""
        if self._call_vision is None:
            try:
                # 这里传入的是单张 base64 + prompt，必须使用单图统一入口；
                # call_llm_api_visual 接受完整 messages，二者不可混用。
                from llm_client import call_vision_api
                self._call_vision = call_vision_api
            except ImportError:
                pass
        return self._call_vision

    async def read(self) -> DataPoint:
        # 检查并重置每日计数
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today_date:
            self._today_captures = 0
            self._today_date = today

        if self._today_captures >= MAX_DAILY_CAPTURES:
            return self._unavailable("daily_cap_reached", expected=True, include_cache=True)

        try:
            from shared_state import get_mobile_relay_callback
            relay = get_mobile_relay_callback()
            if not relay:
                return self._unavailable("no_relay")

            # 1. 便宜检查：屏幕是否亮着
            import uuid
            ca_result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_current_app", {}),
                timeout=5.0,
            )
            if not ca_result or not ca_result.get("success"):
                return self._unavailable("current_app_failed")

            ca_data = ca_result.get("data", {})
            if not isinstance(ca_data, dict) or ca_data.get("available", True) is False:
                return self._unavailable("current_app_unavailable")
            screen_on = ca_data.get("screen_on", False)
            foreground_package = ca_data.get("package") or ""
            foreground_app_name = ca_data.get("app_name") or ""
            if not screen_on:
                return self._unavailable("screen_off", expected=True)

            # 2. 截屏
            ss_result = await asyncio.wait_for(
                relay(str(uuid.uuid4()), "mobile_screenshot", {}),
                timeout=15.0,
            )
            if not ss_result or not ss_result.get("success"):
                return self._unavailable("screenshot_failed")

            image_b64 = _extract_image_base64(ss_result)
            if not image_b64:
                return self._unavailable("no_image")

            # 3. 视觉分类
            vision = await self._get_vision_func()
            if not vision:
                return self._unavailable("no_vision_func")

            import json as _json
            try:
                result = await asyncio.wait_for(
                    vision(
                        image_b64,
                        prompt=VISION_CLASSIFY_PROMPT,
                        model="glm",
                        max_tokens=300,
                    ),
                    timeout=25.0,
                )
            except asyncio.TimeoutError:
                return self._unavailable("vision_timeout")

            # 解析 JSON
            content = ""
            if isinstance(result, dict):
                content = result.get("content", "") or ""
            elif isinstance(result, str):
                content = result

            try:
                # 尝试提取 JSON
                content = content.strip()
                if "```" in content:
                    lines = content.split("\n")
                    content = "\n".join(l for l in lines if not l.startswith("```"))
                parsed = _json.loads(content)
            except (_json.JSONDecodeError, TypeError):
                import re
                m = re.search(r'\{[^}]+\}', content)
                if m:
                    try:
                        parsed = _json.loads(m.group())
                    except (_json.JSONDecodeError, TypeError):
                        return self._unavailable("json_parse_failed")
                else:
                    return self._unavailable("json_parse_failed")

            # 4. 更新缓存
            app_name = parsed.get("app_name", "")
            category = parsed.get("content_category", "unknown")
            activity = parsed.get("activity_type", "unknown")
            description = parsed.get("description", "")
            confidence = float(parsed.get("confidence", 0.5))

            self._last_app_name = app_name
            self._last_category = category
            self._last_activity = activity
            self._last_description = description
            self._last_confidence = confidence
            self._consecutive_failures = 0
            self._last_reason = None
            self._last_error = None
            self._last_success_time = datetime.now().isoformat()
            self._today_captures += 1

            logger.info(
                f"MobileVision: {app_name} | {category} | {activity} | "
                f"{description} | conf={confidence:.2f} | "
                f"daily={self._today_captures}/{MAX_DAILY_CAPTURES}"
            )

            return DataPoint(source=self.name, data={
                "available": True,
                "foreground_package": foreground_package,
                "foreground_app_name": foreground_app_name,
                "app_name": app_name,
                "category": category,
                "activity_type": activity,
                "description": description,
                "confidence": confidence,
            }, captured_at=self._last_success_time)

        except asyncio.TimeoutError:
            return self._unavailable("timeout")
        except Exception as e:
            logger.debug(f"MobileVisionSource 读取失败: {e}")
            return self._unavailable(f"exception:{str(e)[:90]}")

    @property
    def connection_state(self) -> str:
        if self._last_reason in ("screen_off", "daily_cap_reached"):
            return "idle"
        if self._consecutive_failures >= 5:
            return "failed"
        if self._last_description:
            return "connected"
        return "disconnected"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error


# 共享截屏工具函数（供 UserTrackingService 等复用——非破坏性添加）
async def capture_phone_screen_base64(timeout: float = 15.0) -> Optional[str]:
    """通过 relay 获取手机截屏 base64。返回 None 表示失败。"""
    try:
        from shared_state import get_mobile_relay_callback
        relay = get_mobile_relay_callback()
        if not relay:
            return None
        import uuid
        result = await asyncio.wait_for(
            relay(str(uuid.uuid4()), "mobile_screenshot", {}),
            timeout=timeout,
        )
        if not result or not result.get("success"):
            return None
        return _extract_image_base64(result)
    except Exception:
        return None
