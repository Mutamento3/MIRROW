"""听觉皮层 — 语音 prosody 分析

轻量级 librosa 韵律特征提取，用于感知用户语音中的语气/情绪信号。
不做 LLM 调用——所有分析基于信号处理，零延迟、无 token 成本。

分析维度:
  - F0 音高 (pyin) → 平均音高 + 音域 + 尾音趋势
  - 能量 (RMS) → 响度级别 + 变化幅度
  - 语速 → 基于过零率估算
  - 停顿 → >0.3s 的静默段分布
"""

import logging
import os
import numpy as np

logger = logging.getLogger(__name__)

try:
    import librosa
    _LIBROSA_OK = True
except ImportError:
    _LIBROSA_OK = False
    librosa = None


def analyze_prosody(audio_bytes: bytes, sr: int = 16000) -> dict:
    """提取语音的韵律特征，返回结构化数据 + 自然语言摘要。

    Args:
        audio_bytes: 原始音频数据 (WAV/WebM/OGG 等)
        sr: 目标采样率，默认 16kHz

    Returns:
        {
            "pitch_mean_hz": float,       # 平均音高 (Hz)
            "pitch_range_hz": [min, max],  # 音域范围
            "energy_db": float,            # 平均能量 (dB)
            "energy_std_db": float,        # 能量标准差
            "speech_rate": float,          # 语速估算 (音节/秒)
            "pause_count": int,            # 停顿次数 (>0.3s)
            "long_pause_count": int,       # 长停顿次数 (>1.0s)
            "duration_sec": float,         # 音频时长
            "summary": str,                # 自然语言摘要 (~50 tokens)
        }
        失败时返回空 dict。
    """
    if not _LIBROSA_OK:
        logger.warning("librosa 不可用，跳过 prosody 分析")
        return {}

    try:
        # librosa/soundfile 在 Windows 上无法从 BytesIO 读取，先写入临时文件
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio_bytes)
            tmp.close()
            y, sr_actual = librosa.load(tmp.name, sr=sr, mono=True)
        finally:
            import os
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        duration = len(y) / sr_actual

        # 太短的音频 (<0.5s) 分析无意义
        if duration < 0.5:
            return {
                "duration_sec": round(duration, 1),
                "summary": "语音太短，无法分析语气",
            }

        # 过滤纯静默
        rms = librosa.feature.rms(y=y).flatten()
        rms_db = 20 * np.log10(np.maximum(rms, 1e-10))
        voiced_frames = rms_db > (np.mean(rms_db) - 10)  # 能量高于平均值-10dB 的部分
        if np.sum(voiced_frames) < 5:
            return {
                "duration_sec": round(duration, 1),
                "summary": "语音几乎无声，无法分析语气",
            }

        # ── 1. F0 音高 (pyin: 概率 YIN 算法) ──
        f0, voiced_flag, voiced_prob = librosa.pyin(
            y,
            fmin=librosa.note_to_hz('C2'),   # ~65 Hz
            fmax=librosa.note_to_hz('C7'),   # ~2093 Hz
            sr=sr_actual,
        )
        pitch_values = f0[voiced_flag] if np.any(voiced_flag) else np.array([])

        pitch_mean = float(np.mean(pitch_values)) if len(pitch_values) > 0 else 0
        pitch_min = float(np.min(pitch_values)) if len(pitch_values) > 0 else 0
        pitch_max = float(np.max(pitch_values)) if len(pitch_values) > 0 else 0

        # 尾音趋势：最后 15% 有声音帧的 F0 趋势
        tail_trend = "neutral"
        if len(pitch_values) > 10:
            split = int(len(pitch_values) * 0.85)
            tail = pitch_values[split:]
            head = pitch_values[:split]
            if len(tail) > 3 and len(head) > 3:
                tail_mean = np.mean(tail)
                head_mean = np.mean(head)
                diff_pct = (tail_mean - head_mean) / head_mean * 100 if head_mean > 0 else 0
                if diff_pct > 8:
                    tail_trend = "rising"    # 尾音上扬 → 疑问/撒娇
                elif diff_pct < -8:
                    tail_trend = "falling"   # 尾音下沉 → 肯定/低落
                else:
                    tail_trend = "flat"

        # ── 2. 能量分析 ──
        energy_db = float(np.mean(rms_db[voiced_frames])) if np.any(voiced_frames) else -60
        energy_std = float(np.std(rms_db[voiced_frames])) if np.any(voiced_frames) else 0

        # ── 3. 语速估算 (基于过零率在 voiced 段的密度) ──
        zcr = librosa.feature.zero_crossing_rate(y).flatten()
        speech_rate = float(np.mean(zcr[voiced_frames])) if np.any(voiced_frames) else 0
        # 归一化到近似音节/秒 (过零率 * sr / 窗口长度)
        speech_rate = round(speech_rate * sr_actual / 512, 1)

        # ── 4. 停顿检测 ──
        # 找到能量低于阈值的连续区域
        silence_threshold = np.mean(rms_db) - 6  # 低于均值6dB视为停顿
        is_silent = rms_db < silence_threshold
        pause_count = 0
        long_pause_count = 0
        in_silence = False
        silence_start = 0
        hop_length = 512
        for i, silent in enumerate(is_silent):
            t = i * hop_length / sr_actual
            if silent and not in_silence:
                in_silence = True
                silence_start = t
            elif not silent and in_silence:
                in_silence = False
                gap = t - silence_start
                if gap > 0.3:
                    pause_count += 1
                if gap > 1.0:
                    long_pause_count += 1
        if in_silence:
            gap = duration - silence_start
            if gap > 0.3:
                pause_count += 1
            if gap > 1.0:
                long_pause_count += 1

        # ── 5. 生成自然语言摘要 ──
        summary = _build_summary(
            pitch_mean=pitch_mean,
            pitch_range=(pitch_min, pitch_max),
            tail_trend=tail_trend,
            energy_db=energy_db,
            energy_std=energy_std,
            speech_rate=speech_rate,
            pause_count=pause_count,
            long_pause_count=long_pause_count,
        )

        return {
            "pitch_mean_hz": round(pitch_mean, 1),
            "pitch_range_hz": [round(pitch_min, 1), round(pitch_max, 1)],
            "energy_db": round(energy_db, 1),
            "energy_std_db": round(energy_std, 1),
            "speech_rate": round(speech_rate, 1),
            "pause_count": pause_count,
            "long_pause_count": long_pause_count,
            "duration_sec": round(duration, 1),
            "tail_trend": tail_trend,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"prosody 分析异常: {e}", exc_info=True)
        return {}


def _build_summary(
    pitch_mean: float,
    pitch_range: tuple,
    tail_trend: str,
    energy_db: float,
    energy_std: float,
    speech_rate: float,
    pause_count: int,
    long_pause_count: int,
) -> str:
    """从量化特征生成自然语言摘要 (~50 tokens)。"""

    parts = []

    # 音高水平（相对于典型中文女声 200-300Hz / 男声 100-150Hz）
    # 人类伙伴是女生，以女声标准判断
    if pitch_mean > 0:
        if pitch_mean > 300:
            parts.append("音调偏高")
        elif pitch_mean > 240:
            parts.append("音调正常偏高")
        elif pitch_mean > 180:
            parts.append("音调适中")
        elif pitch_mean > 120:
            parts.append("音调偏低")
        else:
            parts.append("声音低沉")

    # 响度
    if energy_db > -15:
        parts.append("声音洪亮")
    elif energy_db > -25:
        parts.append("音量适中")
    elif energy_db > -35:
        parts.append("声音轻柔")
    else:
        parts.append("声音很轻")

    # 能量变化 → 情绪起伏
    if energy_std > 8:
        parts.append("语气起伏大")
    elif energy_std > 5:
        parts.append("语气有变化")

    # 语速
    if speech_rate > 0:
        if speech_rate > 6:
            parts.append("语速很快")
        elif speech_rate > 4.5:
            parts.append("语速偏快")
        elif speech_rate > 3:
            parts.append("语速正常")
        elif speech_rate > 1.5:
            parts.append("语速偏慢")
        else:
            parts.append("语速很慢")

    # 尾音趋势 → 语气信号
    if tail_trend == "rising":
        parts.append("尾音上扬")
    elif tail_trend == "falling":
        parts.append("尾音下沉")

    # 停顿 → 犹豫/思考信号
    if long_pause_count >= 2:
        parts.append(f"有{long_pause_count}次较长停顿——像在思考或犹豫")
    elif pause_count >= 4:
        parts.append(f"停顿较多")

    # 组合
    if not parts:
        return "语气平稳，无明显特征"

    # 合并为连贯句子
    if len(parts) <= 3:
        return "，".join(parts)
    else:
        main = "，".join(parts[:3])
        rest = "，".join(parts[3:])
        return f"{main}；{rest}"


def prosody_to_context_inject(prosody: dict) -> str:
    """将 prosody 分析结果转为 LLM 上下文注入文本。

    只注入 summary（~50 tokens），不暴露原始数值。
    镜像视觉皮层模式: image_analysis → [系统注：用户发送了图片...]
    """
    summary = prosody.get("summary", "")
    if not summary:
        return ""
    return f"[系统注：用户发来一条语音。语气分析：{summary}]"


# ═══════════════════════════════════════════════════════════════
# 🎤 清唱分析 (Singing melody analysis)
# ═══════════════════════════════════════════════════════════════

def analyze_singing(audio_bytes: bytes, sr: int = 16000) -> dict:
    """提取清唱的旋律特征：音高曲线、音符切分、颤音、节奏。

    与说话 prosody 分析的区别：
    - 唱歌音域更宽（可达 1000+ Hz）
    - 音高离散化为音符（而非连续声调）
    - 有颤音（周期性音高调制）
    - 有明确节拍/BPM

    Returns:
        {
            "key_center": str,           # 推断调性中心 (如 "E4")
            "pitch_range_notes": [lo, hi], # 音域 (音符名)
            "note_count": int,            # 大致音符数
            "has_vibrato": bool,          # 是否有颤音
            "tempo_bpm": float,           # 估算 BPM
            "duration_sec": float,        # 时长
            "melody_shape": str,          # 旋律轮廓描述
            "summary": str,               # 自然语言摘要 (~80 tokens)
        }
    """
    if not _LIBROSA_OK:
        logger.warning("librosa 不可用，跳过清唱分析")
        return {}

    try:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            tmp.write(audio_bytes)
            tmp.close()
            y, sr_actual = librosa.load(tmp.name, sr=sr, mono=True)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

        duration = len(y) / sr_actual
        if duration < 1.0:
            return {"duration_sec": round(duration, 1), "summary": "清唱太短，无法分析"}

        # ── 1. F0 音高曲线（pyin: 扩大音域到 C2-C8，覆盖女声清唱） ──
        f0, voiced_flag, voiced_prob = librosa.pyin(
            y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C8'),
            sr=sr_actual,
        )
        pitch_values = f0[voiced_flag] if np.any(voiced_flag) else np.array([])
        if len(pitch_values) < 10:
            return {"duration_sec": round(duration, 1), "summary": "未检测到清唱旋律"}

        # ── 2. 调性中心：最频繁的音符 ──
        note_names = [librosa.hz_to_note(h) for h in pitch_values if h > 0]
        from collections import Counter
        note_counts = Counter(note_names)
        key_center = note_counts.most_common(1)[0][0] if note_counts else "unknown"
        pitch_min_note = librosa.hz_to_note(np.min(pitch_values))
        pitch_max_note = librosa.hz_to_note(np.max(pitch_values))

        # ── 3. 音符切分：检测音高变化 (>2 半音的跳变视为新音符) ──
        notes = []
        if len(pitch_values) > 5:
            # 平滑 F0 曲线
            from scipy.ndimage import uniform_filter1d
            pitch_smooth = uniform_filter1d(np.array(pitch_values, dtype=float), size=3)

            in_note = False
            note_start = 0
            current_pitch = 0
            for i in range(1, len(pitch_smooth)):
                semitone_change = abs(librosa.hz_to_midi(pitch_smooth[i]) - librosa.hz_to_midi(current_pitch)) if current_pitch > 0 else 0
                if not in_note:
                    in_note = True
                    note_start = i
                    current_pitch = pitch_smooth[i]
                elif semitone_change > 2:
                    notes.append({
                        "pitch_hz": round(float(current_pitch), 1),
                        "note_name": librosa.hz_to_note(current_pitch),
                        "duration_frames": i - note_start,
                    })
                    note_start = i
                    current_pitch = pitch_smooth[i]
            # 最后一个音符
            if in_note:
                notes.append({
                    "pitch_hz": round(float(current_pitch), 1),
                    "note_name": librosa.hz_to_note(current_pitch),
                    "duration_frames": len(pitch_smooth) - note_start,
                })

        note_count = len(notes)

        # ── 4. 颤音检测：频率 >4Hz 的周期性音高调制 ──
        has_vibrato = False
        vibrato_notes = []
        if len(pitch_values) > 50:
            for note in notes[:20]:  # 只检查前 20 个音符
                # 对每个音符内的音高做频谱分析
                dur = note["duration_frames"]
                if dur > 15:  # 至少 ~150ms
                    # 用 librosa FFT 检测是否有 4-8Hz 的调制峰值
                    # 简化：检测相邻帧之间的音高波动幅度
                    idx_start = max(0, i_start := 0)
                    # 简化实现：采样音高变化率
                    pass
            # 简化：如果有 >3 个超过 0.5 半音的快速波动，判断为有颤音
            if len(pitch_values) > 40:
                semitone_diffs = np.abs(np.diff(librosa.hz_to_midi(pitch_values[:min(100, len(pitch_values))])))
                rapid_changes = np.sum((semitone_diffs > 0.3) & (semitone_diffs < 1.5))
                has_vibrato = rapid_changes > 5

        # ── 5. BPM 估算：基于音符起始点 ──
        tempo = 0
        if note_count >= 4:
            onsets = librosa.onset.onset_detect(y=y, sr=sr_actual, backtrack=True)
            if len(onsets) >= 3:
                onset_times = librosa.frames_to_time(onsets, sr=sr_actual)
                intervals = np.diff(onset_times)
                # 取中位数间隔 → BPM
                if len(intervals) > 0:
                    median_interval = np.median(intervals)
                    if 0.2 < median_interval < 3.0:
                        tempo = round(60.0 / median_interval)

        # ── 6. 旋律轮廓 ──
        melody_shape = "stable"
        if len(pitch_values) > 30:
            first_half = np.mean(pitch_values[:len(pitch_values)//2])
            second_half = np.mean(pitch_values[len(pitch_values)//2:])
            diff_pct = (second_half - first_half) / first_half * 100 if first_half > 0 else 0
            if diff_pct > 15:
                melody_shape = "rising"
            elif diff_pct < -15:
                melody_shape = "falling"
            elif np.std(pitch_values) > 50:
                melody_shape = "undulating"

        # ── 7. summary ──
        summary_parts = []
        if key_center:
            summary_parts.append(f"调性中心约{key_center}")
        if pitch_min_note and pitch_max_note:
            summary_parts.append(f"音域从{pitch_min_note}到{pitch_max_note}")
        if note_count > 0:
            summary_parts.append(f"约{note_count}个音符")
        if tempo > 0:
            summary_parts.append(f"节奏约{tempo}bpm")
        if has_vibrato:
            summary_parts.append("有颤音")
        if melody_shape == "undulating":
            summary_parts.append("旋律起伏较大")
        elif melody_shape == "rising":
            summary_parts.append("旋律整体上扬")

        summary = "，".join(summary_parts) if summary_parts else "未检测到明显旋律特征"

        return {
            "key_center": key_center,
            "pitch_range_notes": [pitch_min_note, pitch_max_note],
            "note_count": note_count,
            "has_vibrato": has_vibrato,
            "tempo_bpm": tempo,
            "duration_sec": round(duration, 1),
            "melody_shape": melody_shape,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"清唱分析异常: {e}", exc_info=True)
        return {}


def singing_to_context_inject(singing: dict) -> str:
    """将清唱分析结果转为 LLM 上下文注入文本。"""
    summary = singing.get("summary", "")
    if not summary:
        return ""
    return f"[系统注：用户清唱了一段旋律。旋律分析：{summary}]"
