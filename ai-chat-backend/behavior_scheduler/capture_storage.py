"""
截图/快照本地存储

- 保存到 static/sensory_captures/ 目录，通过受控本地 URL 供前端查看
- 自动清理超过 30 天的文件
"""

from __future__ import annotations

import os
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_CAPTURES_DIR: str | None = None
_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 天


def _get_captures_dir() -> str:
    global _CAPTURES_DIR
    if _CAPTURES_DIR is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _CAPTURES_DIR = os.path.join(base, "static", "sensory_captures")
    os.makedirs(_CAPTURES_DIR, exist_ok=True)
    return _CAPTURES_DIR


def _cleanup_old_files():
    """删除超过 30 天的文件"""
    now = time.time()
    captures_dir = _get_captures_dir()
    deleted = 0
    try:
        for fname in os.listdir(captures_dir):
            fpath = os.path.join(captures_dir, fname)
            if not os.path.isfile(fpath):
                continue
            if now - os.path.getmtime(fpath) > _MAX_AGE_SECONDS:
                os.remove(fpath)
                deleted += 1
        if deleted:
            logger.info(f"Capture cleanup: removed {deleted} expired files")
    except Exception as e:
        logger.warning(f"Capture cleanup error: {e}")


def save_and_get_path(prefix: str, image_bytes: bytes, extension: str = "png") -> str:
    """
    保存图片到 captures/ 目录，返回绝对路径。
    每次保存时触发一次过期清理。

    Args:
        prefix: 文件名前缀 (camera / screenshot)
        image_bytes: 图片字节
        extension: 受支持的文件扩展名（png / jpg）
    """
    _cleanup_old_files()

    extension = str(extension or "png").lower().lstrip(".")
    if extension not in {"png", "jpg", "jpeg"}:
        raise ValueError("unsupported capture extension")
    if extension == "jpeg":
        extension = "jpg"
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    fname = f"{prefix}_{timestamp}.{extension}"
    fpath = os.path.join(_get_captures_dir(), fname)

    with open(fpath, "wb") as f:
        f.write(image_bytes)

    logger.info(f"Capture saved: {fpath}")
    return fpath


def attachment_for_path(file_path: str, mime_type: str = "image/png") -> dict | None:
    """Return safe metadata for an owned capture; never expose a disk path."""
    try:
        capture_dir = os.path.realpath(_get_captures_dir())
        resolved = os.path.realpath(file_path)
        if os.path.dirname(resolved) != capture_dir or not os.path.isfile(resolved):
            return None
        filename = os.path.basename(resolved)
        return {
            "url": f"/static/sensory_captures/{filename}",
            "filename": filename,
            "size": os.path.getsize(resolved),
            "type": mime_type,
        }
    except Exception:
        return None
