"""语音 TTS API 路由 — 从 main.py 提取"""

import asyncio
import base64
import hashlib
import logging
import os
import time
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

# TTS 缓存目录（与 send_voice 共用 voice_manager/output/）
_TTS_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "voice_manager", "output")
_CACHE_MAX_AGE_SECONDS = 3 * 24 * 3600  # 3 天


def _tts_cache_path(content_hash: str, ext: str = "wav") -> str:
    """TTS 缓存文件路径（ext：RVC 成功 wav / 回退 mp3）"""
    safe = content_hash[:24]  # 防路径遍历
    return os.path.join(_TTS_OUTPUT_DIR, f"tts_{safe}.{ext}")


def _tts_cache_find(content_hash: str):
    """按 hash 探测已存在的 TTS 缓存（.wav 或 .mp3）。返回 (path, ext) 或 (None, None)。"""
    safe = content_hash[:24]
    for ext in ("wav", "mp3"):
        p = os.path.join(_TTS_OUTPUT_DIR, f"tts_{safe}.{ext}")
        if os.path.isfile(p) and os.path.getsize(p) > 100:
            return p, ext
    return None, None


def _cleanup_old_tts_cache():
    """删除 3 天前的 TTS 缓存文件"""
    try:
        os.makedirs(_TTS_OUTPUT_DIR, exist_ok=True)
        now = time.time()
        removed = 0
        for fname in os.listdir(_TTS_OUTPUT_DIR):
            if not fname.startswith("tts_") or not (fname.endswith(".wav") or fname.endswith(".mp3")):
                continue
            fpath = os.path.join(_TTS_OUTPUT_DIR, fname)
            try:
                if os.path.getmtime(fpath) < now - _CACHE_MAX_AGE_SECONDS:
                    os.unlink(fpath)
                    removed += 1
            except OSError:
                pass
        if removed:
            logger.info(f"TTS 缓存清理: {removed} 个过期文件")
    except Exception as e:
        logger.warning(f"TTS 缓存清理失败: {e}")


@router.post("/api/voice/tts")
async def voice_tts_rest(request: dict):
    """文字转AI的语音 — 支持 content_hash 磁盘缓存"""
    try:
        from voice_manager.tts_pipeline import text_to_voice_sync

        text = (request.get("text", "") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="缺少 text 字段")
        pitch = int(request.get("pitch", -15))
        content_hash = (request.get("content_hash", "") or "").strip()

        # 无 hash → 走旧路径（返回 base64，不缓存）
        if not content_hash:
            audio_bytes, _ext = await asyncio.to_thread(text_to_voice_sync, text, pitch)
            return {
                "audio": base64.b64encode(audio_bytes).decode(),
                "format": _ext,
            }

        # 有 hash → 查磁盘缓存（.wav 或 .mp3）
        cache_path, cache_ext = _tts_cache_find(content_hash)
        if cache_path:
            voice_path = f"/voice/tts_{content_hash[:24]}.{cache_ext}"
            logger.info(f"TTS 缓存命中: {content_hash[:12]}...")
            return {"voice_path": voice_path, "cached": True}

        # 缓存未命中 → 合成（线程池，不阻塞事件循环）→ 落盘
        logger.info(f"TTS 合成中: {content_hash[:12]}... ({len(text)} 字)")
        audio_bytes, _ext = await asyncio.to_thread(text_to_voice_sync, text, pitch)
        os.makedirs(_TTS_OUTPUT_DIR, exist_ok=True)
        cache_path = _tts_cache_path(content_hash, _ext)
        with open(cache_path, "wb") as f:
            f.write(audio_bytes)
        voice_path = f"/voice/tts_{content_hash[:24]}.{_ext}"
        return {"voice_path": voice_path, "cached": False}

    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"语音模块依赖缺失: {e}")
    except Exception as e:
        logger.error(f"TTS 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

