"""音乐分析器 — 多音高转录 + Flash 摘要

通过独立 Python 3.11 venv 子进程调用 basic-pitch (Spotify 多音高检测模型)。
basic-pitch 需要 TensorFlow 2.15 + Python 3.11，与主环境 Python 3.13 隔离。

模式: audio → basic-pitch (子进程) → MIDI JSON → Flash 摘要 → LLM 上下文注入
参考: ttps_pipeline.py 调 RVC 的 subprocess 模式
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Python 3.11 venv 路径 (scoop 安装 + 独立 venv)
_MUSIC_ENV_PYTHON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "music_env", "Scripts", "python.exe"
)

# 子进程脚本 (内联，避免额外文件)
_BASIC_PITCH_WORKER = r"""
import json, sys, os, tempfile
import numpy as np

# basic-pitch 需要在导入前 patch scipy
import scipy.signal
from scipy.signal.windows import gaussian as _gaussian
if not hasattr(scipy.signal, "gaussian"):
    scipy.signal.gaussian = _gaussian

from basic_pitch.inference import predict
from basic_pitch import ICASSP_2022_MODEL_PATH
import pretty_midi

def convert(audio_path, output_json):
    model_path = os.path.join(os.path.dirname(ICASSP_2022_MODEL_PATH), "nmp.onnx")
    if not os.path.exists(model_path):
        model_path = ICASSP_2022_MODEL_PATH

    model_output, midi_data, note_events = predict(audio_path, model_path)

    notes = []
    for inst in midi_data.instruments:
        for note in inst.notes:
            notes.append({
                "pitch": int(note.pitch),
                "note_name": pretty_midi.note_number_to_name(note.pitch),
                "start": round(float(note.start), 3),
                "end": round(float(note.end), 3),
                "duration": round(float(note.end - note.start), 3),
                "velocity": int(note.velocity),
            })

    notes.sort(key=lambda n: n["start"])
    total_duration = round(notes[-1]["end"], 1) if notes else 0

    result = {
        "source": os.path.basename(audio_path),
        "duration_sec": total_duration,
        "total_notes": len(notes),
        "notes": notes,
    }
    with open(output_json, "w") as f:
        json.dump(result, f, indent=2)
    return result

if __name__ == "__main__":
    audio = sys.argv[1]
    output = sys.argv[2]
    result = convert(audio, output)
    print(f"OK {result['total_notes']} notes {result['duration_sec']}s")
"""


async def analyze_music_file(
    audio_bytes: bytes,
    source_name: str = "audio",
    flash_llm_func=None,
) -> dict:
    """对音频文件做多音高转录 + Flash 摘要。

    Args:
        audio_bytes: 原始音频数据 (MP3/WAV/FLAC 等)
        source_name: 来源文件名 (用于日志)
        flash_llm_func: Flash 模型调用函数 async func(prompt) -> str。
                       不传则只返回 JSON，不生成摘要。

    Returns:
        {
            "json": {...},           # whale-listen 风格的完整音符 JSON
            "summary": str,           # Flash 生成的 ~300 token 文学摘要 (仅当传了 flash_llm_func)
            "duration_sec": float,
            "total_notes": int,
        }
    """
    if not os.path.exists(_MUSIC_ENV_PYTHON):
        logger.warning("music_env Python 不可用，跳过音乐分析")
        return {"summary": "音乐分析环境未安装", "json": None}

    try:
        # 写入临时音频文件
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio_tmp:
            audio_tmp.write(audio_bytes)
            audio_path = audio_tmp.name

        # 写入临时 JSON 输出路径
        json_fd, json_path = tempfile.mkstemp(suffix=".json")
        os.close(json_fd)

        try:
            # ── 子进程调用 basic-pitch (Python 3.11 venv) ──
            worker_script = tempfile.NamedTemporaryFile(
                suffix=".py", delete=False, mode="w", encoding="utf-8"
            )
            worker_script.write(_BASIC_PITCH_WORKER)
            worker_script.close()

            proc = await asyncio.create_subprocess_exec(
                _MUSIC_ENV_PYTHON, worker_script.name, audio_path, json_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300.0
            )

            if proc.returncode != 0:
                stderr_text = stderr.decode()[:500] if stderr else ""
                logger.error(f"basic-pitch 子进程失败 (exit {proc.returncode}): {stderr_text}")
                return {"summary": "音乐分析失败", "json": None}

            # ── 读取 JSON 结果 ──
            with open(json_path, "r") as f:
                note_data = json.load(f)

            total_notes = note_data.get("total_notes", 0)
            duration_sec = note_data.get("duration_sec", 0)
            logger.info(
                f"音乐分析完成: {source_name} — {total_notes} notes, {duration_sec}s"
            )

            result = {
                "json": note_data,
                "duration_sec": duration_sec,
                "total_notes": total_notes,
            }

            # ── Flash 生成自然语言摘要 ──
            if flash_llm_func and total_notes > 0:
                summary = await _generate_music_summary(
                    note_data, flash_llm_func
                )
                result["summary"] = summary
            else:
                result["summary"] = ""

            return result

        finally:
            os.unlink(audio_path)
            os.unlink(json_path)
            if os.path.exists(worker_script.name):
                os.unlink(worker_script.name)

    except asyncio.TimeoutError:
        logger.error("basic-pitch 子进程超时 (300s)")
        return {"summary": "音乐分析超时", "json": None}
    except Exception as e:
        logger.error(f"音乐分析异常: {e}", exc_info=True)
        return {"summary": f"音乐分析异常: {str(e)[:100]}", "json": None}


async def _generate_music_summary(note_data: dict, flash_llm_func) -> str:
    """用 Flash 模型从音符 JSON 生成文学性音乐描述 (~300 tokens)。"""

    json_str = json.dumps(note_data, ensure_ascii=False)
    # 截断保护，上限由 truncation_config 控制
    from truncation_config import get_truncation_limit
    _mac = get_truncation_limit("music_analyzer_cap")
    _music_cap = _mac if _mac > 0 else 10**9  # 0=无限制
    if len(json_str) > _music_cap:
        notes = note_data.get("notes", [])
        half = len(notes) // 2
        truncated = {**note_data, "notes": notes[:half] + notes[-half:]}
        truncated["_truncated"] = True
        json_str = json.dumps(truncated, ensure_ascii=False)[:_music_cap]

    prompt = f"""你是音乐分析助手。以下是用户分享的一首音乐的音符数据(JSON格式)。

请用自然语言描述这首音乐的结构特征，像一个人听完后描述感受那样。关注:
- 音域范围（最低到最高的音）
- 音符密度分布（哪里最密/最疏）
- 显著的沉默段落（超过1秒的间隙）
- 调性中心（出现最多的音符）
- 力度变化
- 整体情感色彩

音符数据:
{json_str}

请以"这首曲..."开头，用一段话描述，控制在200字以内。"""

    try:
        from neuron_registry import neuron_trace
        with neuron_trace("music_analysis_summarizer", model="Flash") as trace:
            trace.set_input(prompt[:500])
            summary = await flash_llm_func(prompt)
            trace.set_output(summary[:300] if summary else "")
        return summary.strip()[:500] if summary else ""
    except Exception as e:
        logger.error(f"Flash 音乐摘要生成失败: {e}")
        # 回退：无 Flash 时的简单统计摘要
        notes = note_data.get("notes", [])
        pitches = [n["pitch"] for n in notes]
        if pitches:
            from collections import Counter
            import pretty_midi
            names = [pretty_midi.note_number_to_name(p) for p in pitches]
            top = Counter(names).most_common(3)
            lo = pretty_midi.note_number_to_name(min(pitches))
            hi = pretty_midi.note_number_to_name(max(pitches))
            return f"这首曲共{len(notes)}个音符，{note_data.get('duration_sec', 0):.0f}秒。音域从{lo}到{hi}，中心在{top[0][0]}。"
        return ""


def music_to_context_inject(music_analysis: dict) -> str:
    """将音乐分析结果转为 LLM 上下文注入文本。"""
    summary = music_analysis.get("summary", "")
    if not summary:
        return ""
    return f"[系统注：用户分享了一首音乐。音乐结构分析：{summary}]"
