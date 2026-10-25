"""语音转文字 (STT) — faster-whisper 封装"""
import logging
import io
import wave
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 模型单例
_stt_model = None
_stt_model_lock = threading.Lock()
MODEL_SIZE = "large-v3"  # 中文最优
MODEL_DIR = Path(__file__).parent.parent / "models" / "faster-whisper-large-v3"
# Ambient clips share this model with ordinary voice messages. A bounded
# decoder thread pool leaves CPU capacity for games and the rest of MIRROW.
STT_CPU_THREADS = 2


def _get_model():
    global _stt_model
    if _stt_model is None:
        with _stt_model_lock:
            if _stt_model is None:
                from faster_whisper import WhisperModel
                model_path = str(MODEL_DIR) if MODEL_DIR.exists() else MODEL_SIZE
                logger.info(f"加载 faster-whisper 模型: {model_path}")
                _stt_model = WhisperModel(
                    model_path,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=STT_CPU_THREADS,
                    num_workers=2,
                )
    return _stt_model


def transcribe(audio_bytes: bytes) -> str:
    """将音频字节流转为文字。支持 WAV/OGG/MP3 等常见格式。

    Args:
        audio_bytes: 原始音频数据

    Returns:
        识别出的文字
    """
    import tempfile
    import os

    model = _get_model()

    # faster-whisper 需要文件路径，写入临时文件
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        segments, info = model.transcribe(tmp_path, beam_size=5, language="zh")
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        logger.info(f"STT 完成 | 语言: {info.language} | 概率: {info.language_probability:.2f} | 文字: {text[:80]}...")
        return text
    finally:
        os.unlink(tmp_path)
