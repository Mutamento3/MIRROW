"""Bounded parsing of links supplied by a person or an official share result.

This module does not fetch a URL. A syntactically valid share link is not
evidence that AI has opened, read, or verified the corresponding post.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r"https?://[^\s<>\[\]（）()]+", re.IGNORECASE)
_HOSTS = {"www.xiaohongshu.com", "m.xiaohongshu.com", "xhslink.com"}
_NOTE_PATH = re.compile(r"/(?:explore|discovery/item)/([0-9a-fA-F]{24})(?:/|$)")


def parse_share_link(text: str) -> dict[str, str]:
    """Return only a safe direct/share URL and a conservative identity key."""
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError("分享文字过长或格式不正确")
    match = _URL.search(text)
    if not match:
        raise ValueError("没有找到小红书分享链接")
    candidate = match.group(0).rstrip(".,，。；;!！?？'”’")
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise ValueError("分享链接格式不正确") from None
    if (parts.scheme != "https" or parts.username or parts.password or
            port is not None or host not in _HOSTS):
        raise ValueError("只接受 HTTPS 小红书分享链接")
    if host == "xhslink.com" and not parts.path.strip("/"):
        raise ValueError("短链接缺少帖子路径")
    if len(candidate) > 2048 or "\\" in candidate or any(ord(c) < 32 for c in candidate):
        raise ValueError("分享链接格式不正确")
    note = _NOTE_PATH.search(parts.path)
    # Keep the supplied query for opening the link; never use it as identity.
    safe_url = urlunsplit(("https", host, parts.path, parts.query, ""))
    canonical = urlunsplit(("https", host, parts.path.rstrip("/") or "/", "", ""))
    key = "xhs-note:" + note.group(1).lower() if note else "xhs-link:" + hashlib.sha256(canonical.encode()).hexdigest()[:32]
    return {
        "url": safe_url,
        "post_key": key,
        "link_status": "direct_unverified" if note else "share_unverified",
    }
