"""Edge TTS、可选部署者 RVC 适配器与 EQ；不附私人音色或模型。"""
import os
import asyncio
import tempfile
import random
import logging
import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)

_RVC_PYTHON = os.getenv('RVC_PYTHON', '').strip()
_RVC_INFER = os.getenv('RVC_INFER_SCRIPT', '').strip()
_RVC_DIR = os.path.dirname(os.path.abspath(_RVC_INFER)) if _RVC_INFER else None

_TTS_VOICE = "zh-CN-XiaoyiNeural"
_DEFAULT_PITCH = -15
_DEFAULT_RATE = "-5%"


def _apply_eq(audio: np.ndarray, sr: int) -> np.ndarray:
    """后处理 EQ：温和低频增强 + 保留清脆感 + 降高频噪声"""
    nyq = sr / 2

    b_sub, a_sub = signal.butter(2, 120 / nyq, btype="low")
    sub = signal.lfilter(b_sub, a_sub, audio) * 0.4

    b_warm, a_warm = signal.butter(2, [120 / nyq, 250 / nyq], btype="band")
    warm = signal.lfilter(b_warm, a_warm, audio) * 0.35

    b_pres, a_pres = signal.butter(2, [3000 / nyq, 6000 / nyq], btype="band")
    pres = signal.lfilter(b_pres, a_pres, audio) * 0.2

    b_hi, a_hi = signal.butter(2, 9500 / nyq, btype="low")
    clean = signal.lfilter(b_hi, a_hi, audio)

    b_air, a_air = signal.butter(1, 12000 / nyq, btype="high")
    air = signal.lfilter(b_air, a_air, audio) * 0.08

    enhanced = clean * 0.7 + sub + warm + pres + air
    enhanced = enhanced / max(abs(enhanced)) * 0.78
    return enhanced.astype(np.float32)


async def _run_rvc(input_wav: str, output_wav: str, pitch: int) -> bool:
    if not (os.path.isfile(_RVC_PYTHON) and os.path.isfile(_RVC_INFER)):
        logger.warning(f"RVC 未找到")
        return False
    proc = await asyncio.create_subprocess_exec(
        _RVC_PYTHON, _RVC_INFER, input_wav, output_wav, str(pitch),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=_RVC_DIR,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            logger.error(f"RVC 失败: {stderr.decode()[-200:] if stderr else '?'}")
            return False
        return os.path.isfile(output_wav) and os.path.getsize(output_wav) > 100
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        logger.error("RVC 超时")
        return False


async def text_to_voice(text: str, pitch: int = _DEFAULT_PITCH) -> tuple:
    """文字 → AI 男声音频（Edge TTS + RVC + EQ 后处理）。

    返回 (audio_bytes, ext)：RVC 成功 → "wav"；RVC 失败/超时回退原始 TTS（mp3）→ "mp3"。
    2026-08-23：修复 RVC 回退时返回 mp3 字节却存 .wav 后缀导致无法播放的问题。
    """
    import edge_tts
    import soundfile as sf
    import time as _time

    _t0 = _time.time()
    logger.info(f"TTS: {text[:40]}...")

    # Step 1: Edge TTS 合成
    rate = _DEFAULT_RATE
    communicate = edge_tts.Communicate(text, _TTS_VOICE, rate=rate)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tts_path = f.name
    await communicate.save(tts_path)
    _t1 = _time.time()
    logger.info(f"TTS 完成 ({_t1-_t0:.1f}s)")

    # Step 2: RVC 音色转换
    ok = False
    eq_path = tts_path
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        rvc_path = f.name
    try:
        ok = await _run_rvc(tts_path, rvc_path, pitch)
        _t2 = _time.time()
        if ok:
            # Step 3: EQ 后处理
            audio, sr = sf.read(rvc_path)
            audio = _apply_eq(audio, sr)
            # 转为 int16 PCM（浏览器只能播放整数格式WAV）
            audio_int16 = (audio * 32767).clip(-32768, 32767).astype('int16')
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                eq_path = f.name
            sf.write(eq_path, audio_int16, sr)
            _t3 = _time.time()
            logger.info(f"TTS+RVC+EQ 完成 | TTS={_t1-_t0:.1f}s RVC={_t2-_t1:.1f}s EQ={_t3-_t2:.1f}s total={_t3-_t0:.1f}s")
        else:
            _t2 = _time.time()
            logger.warning(f"RVC 失败/超时({_t2-_t1:.1f}s)，使用原始 TTS")
            eq_path = tts_path

        with open(eq_path, "rb") as f:
            _ext = "wav" if ok else "mp3"  # RVC 成功→WAV；失败回退原始 TTS(mp3)
            return f.read(), _ext
    finally:
        for p in [tts_path, rvc_path]:
            if os.path.isfile(p):
                try: os.unlink(p)
                except OSError: pass
        if ok and os.path.isfile(eq_path):
            try: os.unlink(eq_path)
            except OSError: pass


def text_to_voice_sync(text: str, pitch: int = _DEFAULT_PITCH) -> tuple:
    """同步包装：返回 (audio_bytes, ext)。"""
    return asyncio.run(text_to_voice(text, pitch))
