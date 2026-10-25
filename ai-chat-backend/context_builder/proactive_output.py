"""Deterministic cleanup for proactive-message model output."""

from __future__ import annotations

import re

from .display_markers import strip_display_markers


_HISTORY_MARKER = re.compile(r"^(\s*[\[【［][^\]】］]{1,40}[\]】］]\s*)(.*)$", re.DOTALL)
_SPEAKER_PREFIX = re.compile(r"^\s*(?:人类伙伴|AI)\s*[：:]\s*", re.IGNORECASE)


def clean_proactive_output(content: str) -> str:
    """Return raw message body; UI owns sender/type/timestamp presentation."""
    from visible_reply import clean_visible_reply
    return clean_visible_reply(content)


def strip_leading_assistant_label(content: str) -> str:
    """去掉模型模仿的行首 AI 说话人标签，不触碰正文中的正常提及。"""
    return strip_display_markers(re.sub(r"^ {0,3}[Kk][ \t]*[：:][ \t]*", "", str(content or ""), count=1))


def format_push_history_line(role: str, content: str) -> str:
    """给折叠为 system 文本的主动消息历史补充说话人；主聊天无需调用。"""
    text = str(content or "").strip()
    if not text or role not in {"user", "assistant"}:
        return text
    speaker = "人类伙伴：" if role == "user" else "AI："
    marker = _HISTORY_MARKER.match(text)
    if marker:
        body = _SPEAKER_PREFIX.sub("", marker.group(2), count=1)
        return f"{marker.group(1)}{speaker}{body}"
    body = _SPEAKER_PREFIX.sub("", text, count=1)
    return f"{speaker}{body}"
