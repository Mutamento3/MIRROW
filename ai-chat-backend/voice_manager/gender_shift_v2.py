"""女→男音色转换 V2 — 降噪 + 共振峰 + EQ"""
import sys, os, argparse
import numpy as np
import soundfile as sf
import noisereduce as nr
import librosa
import parselmouth
from scipy.signal import butter, lfilter


def eq_band(audio: np.ndarray, sr: int, low_hz: float, high_hz: float,
            gain_db: float) -> np.ndarray:
    """简易 EQ 频段增益"""
    nyq = sr / 2
    low = low_hz / nyq
    high = high_hz / nyq
    # 带通滤波器提取目标频段
    b, a = butter(2, [low, high], btype='band')
    band = lfilter(b, a, audio)
    # 混回原信号
    gain = 10 ** (gain_db / 20)
    return audio + band * (gain - 1)


def clean_audio(audio: np.ndarray, sr: int) -> np.ndarray:
    audio = nr.reduce_noise(y=audio, sr=sr, prop_decrease=0.8)
    audio, _ = librosa.effects.trim(audio, top_db=25)
    peak = np.abs(audio).max()
    if peak > 0:
        audio = audio / peak * 0.9
    return audio


def shift_gender(audio: np.ndarray, sr: int,
                 pitch_semitones: float = -7.0,
                 formant_ratio: float = 0.78,
                 eq_boost_hz: tuple = None,
                 eq_gain_db: float = 2.0) -> np.ndarray:
    """共振峰 + 音高 + EQ"""
    # 1. 共振峰
    snd = parselmouth.Sound(audio, sampling_frequency=sr)
    snd = parselmouth.praat.call(snd, "Change gender", 75, 600, formant_ratio, 0, 1, 1)
    audio = snd.values[0].astype(np.float32)

    # 2. 音高
    audio = librosa.effects.pitch_shift(y=audio, sr=sr, n_steps=pitch_semitones)

    # 3. EQ（胸腔共鸣）
    if eq_boost_hz:
        audio = eq_band(audio, sr, eq_boost_hz[0], eq_boost_hz[1], eq_gain_db)

    # 防削波
    peak = np.abs(audio).max()
    if peak > 0.99:
        audio = audio / peak * 0.95

    return audio


PRESETS = {
    "user_params":  {"pitch": -12, "formant": 0.65, "eq": (400, 500), "eq_gain": 2.5},
    "user_params2": {"pitch": -12, "formant": 0.68, "eq": (400, 500), "eq_gain": 3.0},
    "user_params3": {"pitch": -12, "formant": 0.65, "eq": (350, 500), "eq_gain": 1.5},
    "medium_plus":  {"pitch": -7,  "formant": 0.78, "eq": (400, 500), "eq_gain": 2.0},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="输入 WAV")
    parser.add_argument("--output-dir", default="./models/gender_test_v2")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    audio, sr = sf.read(args.input, dtype='float32')
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    print(f"原始: {len(audio)/sr:.1f}s")

    audio = clean_audio(audio, sr)
    print(f"降噪后: {len(audio)/sr:.1f}s")

    os.makedirs(args.output_dir, exist_ok=True)

    for name, cfg in PRESETS.items():
        print(f"\n{name}: pitch={cfg['pitch']}, formant={cfg['formant']}, EQ={cfg.get('eq')} +{cfg.get('eq_gain',2)}dB")
        result = shift_gender(audio, sr, cfg['pitch'], cfg['formant'],
                              cfg.get('eq'), cfg.get('eq_gain', 2))
        path = os.path.join(args.output_dir, f"k_{name}.wav")
        sf.write(path, result, sr)
        print(f"  -> {path}")

    print(f"\n完成！{args.output_dir}/")


if __name__ == "__main__":
    main()
