"""
刷手机模式 — 陪刷后台循环

PhoneBrowser 单例：截图 → pHash去重 → 共用视觉合同一次分析与自然表达 → 推送。
陪刷消息是正常 assistant 消息，无特殊前缀，无特殊标记。
"""

import asyncio
from body_admission import optional_observation
import base64
import io
import os
import time
import uuid
import logging
from datetime import datetime
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# 使用项目统一日志模块（有 Lock + 已验证可写）
from context_scheduler_log import write_log as _pb_log

# ── pHash 依赖（可选，未安装时回退到简单MD5） ──
try:
    from PIL import Image
    import imagehash
    _HAS_IMAGEHASH = True
except ImportError:
    _HAS_IMAGEHASH = False
    import hashlib


# ── 模块单例 ──
_phone_browser: Optional["PhoneBrowser"] = None


def get_phone_browser() -> Optional["PhoneBrowser"]:
    return _phone_browser


# ── 可注入的外部依赖（由 main.py startup 设置） ──
_push_message_callback: Optional[Callable] = None  # async fn(str) → 写入+推送
_vision_llm_func: Optional[Callable] = None         # async fn(image_b64, prompt) → str（flash-vision 一次调用）


class PhoneBrowser:
    """陪刷模式后台循环"""

    DEFAULT_INTERVAL_MINUTES = 5           # 默认截图间隔（分钟）
    PHASH_SIMILARITY_THRESHOLD = 5         # 汉明距离 ≤5 视为同一画面

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cooldown_until = 0.0           # monotonic, 下次截图的最早时间
        self._last_phash = None              # 上次截图的 ImageHash
        self._same_content_since: Optional[float] = None  # 同画面起始 monotonic
        self._last_message_time: Optional[float] = None  # 上次发消息的 monotonic（计算实际间隔）
        self._mobile_relay: Optional[Callable] = None

    # ── 属性 ──

    @property
    def interval_minutes(self) -> int:
        """实时从 settings 读取间隔（分钟），支持运行时变更"""
        try:
            from settings_manager import get_setting
            val = get_setting("phone_browse_interval")
            if isinstance(val, (int, float)) and 1 <= val <= 10:
                return int(val)
        except Exception:
            pass
        return self.DEFAULT_INTERVAL_MINUTES

    # ── 生命周期 ──

    async def start(self):
        """启动后台循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"PhoneBrowser started (interval={self.interval_minutes}min)")

    async def stop(self):
        """停止后台循环"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("PhoneBrowser stopped")

    def set_active(self, enabled: bool):
        """专用模式接口在状态变化时初始化冷却；执行准入读取 shared_state。"""
        from shared_state import get_phone_browse_mode, get_mobile_connected
        if enabled:
            # 初始化 CD：开启后等 interval 才第一次自动截图
            self._cooldown_until = time.monotonic() + self.interval_minutes * 60
        logger.info(f"PhoneBrowser.set_active({enabled}), mode={get_phone_browse_mode()}, mobile={get_mobile_connected()}, CD={self._cooldown_until}")

    async def summon(self):
        """悬浮球召唤：截图 → 立即返回 → 后台继续分析推送"""
        _pb_log("[PHONE_BROWSE] summon() 开始")
        self._same_content_since = None
        self._last_phash = None
        self._last_message_time = None

        image_b64 = await self._capture_screenshot()
        if not image_b64:
            _pb_log("[PHONE_BROWSE] summon 截图失败")
            return
        _pb_log(f"[PHONE_BROWSE] summon 截图完成 ({len(image_b64)} chars)，启动后台处理")

        # 后续分析推送异步跑，不等
        async def _safe_process():
            try:
                await self._process_captured(image_b64)
            except Exception as e:
                _pb_log(f"[PHONE_BROWSE] summon 后台任务异常: {e}")
                import traceback
                traceback.print_exc()
                self._cooldown_until = time.monotonic() + self.interval_minutes * 60
        asyncio.create_task(_safe_process())

    # ── 主循环 ──

    async def _capture_and_push(self):
        """auto-loop 用：截图→完整处理"""
        image_b64 = await self._capture_screenshot()
        if not image_b64:
            return
        await self._process_captured(image_b64)

    @optional_observation
    async def _process_captured(self, image_b64: str):
        """对已截图的 base64 执行 pHash→flash-vision 一次调用→推送→设CD（summon 和 loop 共用）"""
        _pb_log("[PHONE_BROWSE] Step 2: pHash...")
        same_content, duration_minutes = self._compare_phash(image_b64)
        _pb_log(f"[PHONE_BROWSE] Step 2 OK: same={same_content} dur={duration_minutes}min")

        # Step 3: 先尝试 flash-vision 一次调用（看图+生成一条龙，省一次调用）
        _pb_log("[PHONE_BROWSE] Step 3: flash-vision 一次调用...")
        result = await self._generate_message_with_image(
            image_b64,
            same_content=same_content,
            same_duration_minutes=duration_minutes,
        )
        if not result:
            _pb_log("[PHONE_BROWSE] Step 4 FAIL: 生成返回空")
            self._cooldown_until = time.monotonic() + self.interval_minutes * 60
            return
        message = result.get("content", "")
        thinking = result.get("reasoning")
        if not message:
            _pb_log("[PHONE_BROWSE] Step 4 FAIL: content为空")
            self._cooldown_until = time.monotonic() + self.interval_minutes * 60
            return
        _pb_log(f"[PHONE_BROWSE] Step 4 OK: {message[:80]}...")

        _pb_log("[PHONE_BROWSE] Step 5: 推送...")
        await self._push_message(message, thinking=thinking)
        self._last_message_time = time.monotonic()
        _pb_log("[PHONE_BROWSE] Step 5 OK: 已推送")

        # 统一设 CD：无论来自 summon 还是 loop，执行完后等 interval 才能再触发
        self._cooldown_until = time.monotonic() + self.interval_minutes * 60

    async def _loop(self):
        """后台循环：每 1s 检查条件，CD 到了就执行截图→分析→推送"""
        while self._running:
            try:
                await asyncio.sleep(1)
                if not self._should_capture():
                    continue

                await self._capture_and_push()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"PhoneBrowser._loop error: {e}", exc_info=True)

    def _should_capture(self) -> bool:
        from shared_state import get_phone_browse_mode, get_phone_browse_paused, get_mobile_connected
        if not get_phone_browse_mode():
            return False
        if get_phone_browse_paused():
            return False
        if not get_mobile_connected():
            return False
        if time.monotonic() < self._cooldown_until:
            return False
        return True

    # ── 1. 截图 ──

    async def _capture_screenshot(self) -> Optional[str]:
        """通过 mobile relay 获取手机截屏 base64"""
        if not self._mobile_relay:
            logger.warning("PhoneBrowser: mobile_relay not set")
            return None
        try:
            result = await asyncio.wait_for(
                self._mobile_relay(str(uuid.uuid4()), "mobile_screenshot", {}),
                timeout=30.0,
            )
            b64 = result.get("image_base64", "") if isinstance(result, dict) else ""
            if b64:
                logger.info(f"PhoneBrowser: screenshot captured ({len(b64)} chars base64)")
            return b64
        except asyncio.TimeoutError:
            logger.warning("PhoneBrowser: screenshot relay timeout")
            return None
        except Exception as e:
            logger.warning(f"PhoneBrowser: screenshot relay error: {e}")
            return None

    # ── 2. pHash 对比 ──

    def _compare_phash(self, image_b64: str) -> tuple:
        """返回 (same_content: bool, same_duration_minutes: int)"""
        try:
            img_bytes = base64.b64decode(image_b64)
            img = Image.open(io.BytesIO(img_bytes))

            if _HAS_IMAGEHASH:
                h = imagehash.phash(img)
                if self._last_phash is not None:
                    dist = h - self._last_phash
                    if dist <= self.PHASH_SIMILARITY_THRESHOLD:
                        if self._same_content_since is None:
                            self._same_content_since = time.monotonic()
                        duration = int((time.monotonic() - self._same_content_since) / 60)
                        self._last_phash = h
                        return (True, duration)
                    else:
                        self._same_content_since = None
                self._last_phash = h
                return (False, 0)
            else:
                # 回退：MD5 精确匹配
                h = hashlib.md5(img_bytes).hexdigest()
                if self._last_phash is not None:
                    if h == self._last_phash:
                        if self._same_content_since is None:
                            self._same_content_since = time.monotonic()
                        duration = int((time.monotonic() - self._same_content_since) / 60)
                        return (True, duration)
                    else:
                        self._same_content_since = None
                self._last_phash = h
                return (False, 0)
        except Exception as e:
            logger.warning(f"PhoneBrowser pHash error: {e}")
            return (False, 0)


    # ── 4.5 flash-vision 一次调用（图片+文字一条龙，省一次调用）──

    async def _generate_message_with_image(
        self, image_b64: str,
        same_content: bool = False, same_duration_minutes: int = 0,
    ) -> Optional[dict]:
        """共用视觉对话入口；失败或未注入时返回 None，不推送假回复。"""
        global _vision_llm_func
        if not _vision_llm_func:
            _pb_log("[PHONE_BROWSE] vision_llm_func 未注入，跳过合并路径")
            return None

        same_hint = ""
        if same_content and same_duration_minutes > 10:
            same_hint = "（她还在同一个画面）"

        prompt = (
            "人类伙伴正在刷手机，你每隔一会看一眼她的屏幕陪她。"
            "这次你看到的是这个界面，请你仔细看看人类伙伴在看什么，"
            "然后以朋友好奇地凑过来看她的手机屏幕的语气，直接跟她说一句自然的陪伴话。\n\n"
            "本次屏幕观测：" + (same_hint if same_hint else "新取得的画面") + "。"
        ).strip()

        from neuron_registry import neuron_trace
        with neuron_trace("phone_browse_vision", recipe="PHONE_BROWSE", model="flash-vision") as trace:
            trace.set_input(prompt[:300])
            try:
                result = await _vision_llm_func(image_b64, prompt)
                if isinstance(result, dict):
                    message = (result.get("content") or "").strip()
                    reasoning = result.get("reasoning")
                else:
                    message = str(result).strip() if result else ""
                    reasoning = None
                if message:
                    trace.set_output(message[:300])
                    _pb_log(f"[PHONE_BROWSE] vision 一次调用 OK: {message[:80]}...")
                    return {"content": message, "reasoning": reasoning}
                _pb_log("[PHONE_BROWSE] vision 一次调用返回空")
            except Exception as e:
                trace.set_error(str(e))
                _pb_log(f"[PHONE_BROWSE] vision 一次调用失败: {e}")
        return None

    # ── 5. 推送 ──

    async def _push_message(self, message: str, thinking: Optional[str] = None):
        """写入存储 + 推送到前端"""
        global _push_message_callback
        if _push_message_callback:
            await _push_message_callback(message, thinking=thinking)
        else:
            logger.warning("PhoneBrowser: push callback not set, message dropped")


# ── 模块级设置函数（main.py startup 调用） ──

def setup_phone_browser(
    mobile_relay: Callable,
    push_callback: Callable,
    vision_llm_func: Optional[Callable] = None,
):
    """初始化 PhoneBrowser 并注入依赖

    vision_llm_func：图片+文字一次调用 flash-vision 生成陪刷消息（省一次调用）。
    主模型与回退共用视觉输出合同；未注入时保持失败，不生成摘要替身。
    """
    global _phone_browser, _push_message_callback, _vision_llm_func
    _push_message_callback = push_callback
    _vision_llm_func = vision_llm_func

    browser = PhoneBrowser()
    browser._mobile_relay = mobile_relay
    _phone_browser = browser
    return browser
