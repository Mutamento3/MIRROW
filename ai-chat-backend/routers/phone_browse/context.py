"""
刷手机陪聊 — 上下文构建

为官方 Flash 生成陪刷消息时构建上下文：
  1. AI 人设（核心 300 字符）
  2. Affect v2 自我状态（只读自然语言）
  3. 时间感知（HH:MM，星期X）
  4. 离开时长（人类伙伴离开 MIRROW 多久）
  5. 近期对话（最近 6 条）

参照群聊 k_context.py 的上下文构建模式，精简为陪刷场景所需。
"""

import logging
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── 模块级依赖（由 main.py startup 注入） ──
_persona_provider: Optional[Callable] = None          # () -> str
_session_messages_provider: Optional[Callable] = None  # async (int) -> str  返回最近 N 条格式化文本
_page_last_seen_provider: Optional[Callable] = None    # () -> float | None  返回 monotonic 时间戳


def setup_phone_browse_context(
    persona_provider: Callable,
    session_messages_provider: Callable,
    page_last_seen_provider: Callable,
):
    """由 main.py startup 调用一次，注入依赖"""
    global _persona_provider, _session_messages_provider, _page_last_seen_provider
    _persona_provider = persona_provider
    _session_messages_provider = session_messages_provider
    _page_last_seen_provider = page_last_seen_provider


def _safe_error_type(exc: Exception) -> str:
    category = type(exc).__name__
    return category if category in {"RuntimeError", "TypeError", "ImportError", "OSError", "TimeoutError"} else "Exception"


async def _read_recent_history() -> dict:
    """一次取得来源快照；缺来源、成功空值和失败各有独立状态。"""
    provider = _session_messages_provider
    if provider is None:
        return {"status": "not_configured", "text": ""}
    try:
        text = await provider(6)
        if not isinstance(text, str):
            raise TypeError("Recent history provider must return text")
        return {"status": "ok", "text": text}
    except Exception as exc:
        error_type = _safe_error_type(exc)
        logger.warning("PhoneBrowse recent history unavailable error_type=%s", error_type)
        return {"status": "error", "text": "", "error_type": error_type}


async def build_phone_browse_messages() -> list[dict]:
    """保留身份、历史、动态事实的完整消息序列。"""
    import os
    recent_snapshot = await _read_recent_history()
    recent_kwargs = {}
    if recent_snapshot["status"] != "not_configured":
        recent_kwargs["recent_messages"] = recent_snapshot["text"]
        if recent_snapshot["status"] == "error":
            recent_kwargs["recent_messages_error_type"] = recent_snapshot["error_type"]
    if os.environ.get("MIRROW_USE_CONTEXT_BUILDER") == "1":
        try:
            from context_builder import ContextBuilder
            persona = ""
            if _persona_provider:
                persona = _persona_provider() or ""
            if not persona:
                from settings_manager import get_ai_persona_simplified
                persona = get_ai_persona_simplified() or ""
            result = await ContextBuilder.build("PHONE_BROWSE",
                persona=persona, mood="",
                **recent_kwargs)
            from context_builder.assembly import assemble_context_result
            return assemble_context_result(result)
        except Exception as e:
            logger.warning("PhoneBrowse Builder failed, using legacy error_type=%s", _safe_error_type(e))

    parts = []
    identity = ""
    history = ""

    # ── 1. AI 人设 ──
    try:
        persona = ""
        if _persona_provider:
            persona = _persona_provider() or ""
        if not persona:
            from settings_manager import get_ai_persona_simplified
            persona = get_ai_persona_simplified() or ""
        if persona:
            from truncation_config import get_truncation_limit
            _pcap = get_truncation_limit("persona_anchor_cap")
            _pcap = _pcap if _pcap > 0 else 300
            identity = persona[:_pcap]
    except Exception as e:
        logger.warning(f"PhoneBrowse context: persona 获取失败: {e}")

    # ── 2. Affect v2 自我状态 ──
    try:
        from affect import get_affect_service
        affect_text = get_affect_service().render_self(cap=500)
        if affect_text:
            parts.append(affect_text)
    except Exception as e:
        logger.debug(f"PhoneBrowse context: Affect 获取失败: {e}")

    # ── 3. 时间感知 ──
    try:
        now = datetime.now()
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        weekday = weekday_names[now.weekday()]
        parts.append(f"现在是{now.strftime('%H:%M')}，星期{weekday}")
    except Exception:
        pass

    # ── 4. 近期对话：回退复用同轮快照，不重复读取或换来源 ──
    if recent_snapshot["status"] == "ok":
        if recent_snapshot["text"]:
            history = f"【最近聊天记录】\n{recent_snapshot['text']}"
    elif recent_snapshot["status"] == "error":
        parts.append("近期对话来源本轮读取失败，聊天记录情况未知。")
    else:
        parts.append("近期对话来源本轮未接入，聊天记录情况未知。")

    messages = [{"role": "system", "content": identity}] if identity else []
    if history:
        messages.append({"role": "system", "content": history})
    if parts:
        messages.append({"role": "system", "content": "\n".join(parts)})
    return messages


async def build_phone_browse_context() -> str:
    """兼容文本诊断入口；生成入口直接使用完整 messages。"""
    from types import SimpleNamespace
    from context_builder.assembly import assemble_context_text
    return assemble_context_text(SimpleNamespace(formatted_messages=await build_phone_browse_messages()))
