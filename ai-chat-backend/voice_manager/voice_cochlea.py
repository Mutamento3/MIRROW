"""语音耳蜗 — subprocess 调 mubai-ears cochlea.py + 共享状态供 context_builder 消费。

两层职责:
  1. 运行时: subprocess 调原版脚本，生成频谱图 + 韵律 JSON
  2. 上下文: 共享状态 —— main.py 写入 → context_builder ingredient 读取并自清

不移植算法，直接用 mubai-ears (MIT, HMH323) 原版脚本。
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MUBAI_EARS_DIR = Path(__file__).parent / "mubai_ears"
_COCHLEA_SCRIPT = _MUBAI_EARS_DIR / "cochlea.py"
_OUT_DIR = Path(__file__).parent / "output" / "spectrogram"

# ── 共享状态（供 context_builder ingredient 消费）──
_current_prosody: dict | None = None
_current_prosody_id: str = ""
_current_prosody_set_at: float = 0.0
_PROSODY_TTL_SECONDS = 180.0


def set_current_prosody(data: dict | None):
    """main.py 在调 context_scheduler 之前调用，写入当前语音的 prosody 数据。

    ingredient 读取后自动清除，因此不会跨轮残留。
    """
    global _current_prosody, _current_prosody_id, _current_prosody_set_at
    _current_prosody = data
    if data is None:
        _current_prosody_id = ""
        _current_prosody_set_at = 0.0
    else:
        # A caller may provide a request/message id.  Otherwise create a
        # process-local stable id used by claim+commit across repeated builds.
        _current_prosody_id = str(
            data.get("prosody_id") or data.get("message_id") or data.get("source_id") or ""
        ).strip() or f"prosody_{id(data)}"
        _current_prosody_set_at = time.monotonic()


def peek_prosody() -> dict | None:
    """Read a fresh request snapshot without consuming it.

    Failed/cancelled requests may retry briefly, but an abandoned voice turn
    must not leak into unrelated later text or proactive messages.
    """
    if (
        _current_prosody is not None
        and _current_prosody_set_at > 0
        and time.monotonic() - _current_prosody_set_at > _PROSODY_TTL_SECONDS
    ):
        return None
    return _current_prosody


def get_current_prosody_id() -> str:
    """Return the stable id associated with the current snapshot."""
    return _current_prosody_id if peek_prosody() is not None else ""


def commit_prosody(stable_id: str) -> bool:
    """Consume a snapshot only if it is still the claimed version.

    A repeated commit is a no-op, while a newer request cannot be consumed by
    an older provider callback.
    """
    global _current_prosody, _current_prosody_id, _current_prosody_set_at
    if not stable_id or str(stable_id) != _current_prosody_id:
        return False
    _current_prosody = None
    _current_prosody_id = ""
    _current_prosody_set_at = 0.0
    return True


def get_and_clear_prosody() -> dict | None:
    """ingredient 调用：读取并清除（单次消费，防止跨轮残留）。"""
    global _current_prosody, _current_prosody_id, _current_prosody_set_at
    data = _current_prosody
    _current_prosody = None
    _current_prosody_id = ""
    _current_prosody_set_at = 0.0
    return data


# ═══════════════════════════════════════════════════════════
# 语气分类（纯规则，零 LLM）+ 个人音高基线校准
# ═══════════════════════════════════════════════════════════

_BASELINE_FILE = Path(__file__).resolve().parent.parent / "data" / "voice_baseline.json"
_DEFAULT_BASELINE = 240.0  # 通用女声基线，首次使用


def _load_baseline() -> dict:
    """读个人音高基线。损坏/缺失回退默认。"""
    try:
        if _BASELINE_FILE.exists():
            with open(_BASELINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("pitch_baseline"), (int, float)):
                return data
    except Exception as e:
        logger.warning(f"读语音基线失败: {e}")
    return {"pitch_baseline": _DEFAULT_BASELINE, "sample_count": 0}


def _save_baseline(data: dict):
    """写个人音高基线。失败静默（分类回退默认基线）。"""
    try:
        _BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"写语音基线失败: {e}")


def _update_baseline(pitch_mean: float):
    """滚动更新个人音高基线（0.9 旧 + 0.1 新）。只接受合理音高（100-600Hz）。"""
    if not (100 <= pitch_mean <= 600):
        return
    data = _load_baseline()
    baseline = float(data["pitch_baseline"])
    count = int(data.get("sample_count", 0))
    new_baseline = baseline * 0.9 + pitch_mean * 0.1
    _save_baseline({"pitch_baseline": round(new_baseline, 1), "sample_count": count + 1})


def classify_tone(prosody: dict) -> str:
    """从 prosody 特征推断语气标签（纯规则）。

    音高判断相对个人基线（人类伙伴声音高音域），能量/语速用绝对阈值（与音域无关）。

    Args:
        prosody: auditory_cortex.analyze_prosody 的返回 dict

    Returns:
        语气标签：撒娇/兴奋/低落/焦虑/疲惫/温柔/轻快/沉重/慵懒/平静
    """
    if not prosody:
        return "平静"

    pitch = prosody.get("pitch_mean_hz", 0) or 0
    energy = prosody.get("energy_db", -20) or -20
    energy_std = prosody.get("energy_std_db", 0) or 0
    speech_rate = prosody.get("speech_rate", 4) or 4
    pause_count = prosody.get("pause_count", 0) or 0
    tail_trend = prosody.get("tail_trend", "flat") or "flat"

    # 无有效音高（纯静默/太短）→ 平静
    if pitch <= 0:
        return "平静"

    # 更新个人基线（滚动平均）
    _update_baseline(pitch)

    baseline = float(_load_baseline()["pitch_baseline"])
    hi_thr = baseline * 1.20   # 偏高 20%
    lo_thr = baseline * 0.80   # 偏低 20%

    pitch_high = pitch > hi_thr
    pitch_low = pitch < lo_thr
    energy_high = energy > -18
    energy_low = energy < -30
    rate_fast = speech_rate > 5
    rate_slow = speech_rate < 3
    pause_many = pause_count >= 3
    pause_few = pause_count <= 1

    # 优先级从上到下，命中即返回
    if tail_trend == "rising" and pitch_high and rate_slow:
        return "撒娇"
    if pitch_high and energy_high and rate_fast:
        return "兴奋"
    if pitch_low and energy_low and tail_trend == "falling":
        return "低落"
    if rate_fast and pause_many and energy_std > 8:
        return "焦虑"
    if energy_low and rate_slow and pitch_low:
        return "疲惫"
    if energy_low and not pitch_high and not pitch_low and pause_few:
        return "温柔"
    if pitch_high and rate_fast:
        return "轻快"
    if pitch_low and rate_slow and pause_many:
        return "沉重"
    if rate_slow and energy_low and tail_trend == "flat":
        return "慵懒"
    return "平静"


# ── Subprocess 调用 ──

def run_cochlea_sync(audio_bytes: bytes, voice_hash: str, persist_spectrogram: bool = True) -> dict:
    """同步调用 mubai-ears cochlea.py，读取输出文件。

    1. 写临时 WAV → 2. subprocess.run cochlea.py → 3. 读 out/<stem>/summary.json + listen.png

    Returns:
        {"ok": bool, "summary_json": dict, "spectrogram_b64": str,
         "spectrogram_path": str, "pitch_contour": list, "duration_sec": float, "error": str}
    """
    tmp = None
    try:
        # 1. 写临时 WAV（mubai-ears 从文件读，不从 stdin）
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(audio_bytes)
        tmp.close()

        # 2. 调原版脚本
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [os.sys.executable, str(_COCHLEA_SCRIPT), tmp.name],
            cwd=str(_MUBAI_EARS_DIR),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"cochlea.py 失败: {result.stderr[:500]}"}

        # 3. 读输出文件（mubai-ears 输出到 out/<文件名主干>/）
        stem = Path(tmp.name).stem
        out_dir = _MUBAI_EARS_DIR / "out" / stem
        summary_path = out_dir / "summary.json"
        png_path = out_dir / "listen.png"

        if not summary_path.exists():
            return {"ok": False, "error": "summary.json 未生成"}

        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)

        spectrogram_b64 = ""
        if png_path.exists() and persist_spectrogram:
            with open(png_path, "rb") as f:
                spectrogram_b64 = base64.b64encode(f.read()).decode()

            # 同时复制到 MIRROW 的静态目录供直接访问（/voice/spectrogram/{hash}.png）
            dest = _OUT_DIR / f"{voice_hash}.png"
            shutil.copy(png_path, dest)

        return {
            "ok": True,
            "summary_json": summary,
            "pitch_contour": summary.get("pitch_contour", []),
            "spectrogram_b64": spectrogram_b64,
            "spectrogram_path": f"/voice/spectrogram/{voice_hash}.png" if spectrogram_b64 else "",
            "duration_sec": summary.get("duration_sec", 0),
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "cochlea.py 执行超时（30s）"}
    except Exception as e:
        logger.error(f"cochlea 分析异常: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        # 环境监听只消费数值韵律摘要；不需要把频谱图留在静态目录或
        # mubai-ears 的临时输出树中。普通语音消息沿用原有持久化行为。
        if not persist_spectrogram and tmp:
            try:
                stem = Path(tmp.name).stem
                shutil.rmtree(_MUBAI_EARS_DIR / "out" / stem, ignore_errors=True)
                (_OUT_DIR / f"{voice_hash}.png").unlink(missing_ok=True)
            except Exception:
                pass


async def run_cochlea(audio_bytes: bytes, voice_hash: str) -> dict:
    """run_cochlea_sync 的异步包装，不阻塞事件循环。"""
    return await asyncio.to_thread(run_cochlea_sync, audio_bytes, voice_hash)
