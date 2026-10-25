"""从视频/音频文件中提取音频，转为 16kHz 单声道 WAV
支持: MP3, MP4, M4A, WAV, MKV, MOV, OGG, FLAC 等
用法: python extract_audio.py <文件路径> [输出路径]
"""
import sys
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 项目自带的 ffmpeg 二进制
_FFMPEG = os.path.join(os.path.dirname(__file__), "..", "models", "ffmpeg.exe")
if not os.path.isfile(_FFMPEG):
    _FFMPEG = "ffmpeg"  # fallback to system PATH


def extract_audio(video_path: str, output_path: str = None) -> str:
    """用 ffmpeg 提取音频，容错性最强（支持损坏的 MP3 头部）"""
    if output_path is None:
        basename = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(os.path.dirname(__file__), "output", f"{basename}.wav")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = [
        _FFMPEG,
        "-i", video_path,
        "-ac", "1",           # 单声道
        "-ar", "16000",        # 16kHz
        "-sample_fmt", "s16",  # 16-bit PCM
        "-y",                  # 覆盖
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # ffmpeg 有时即使成功也返回非零，检查输出文件
        if not os.path.isfile(output_path) or os.path.getsize(output_path) < 100:
            print(f"ffmpeg 错误:\n{result.stderr}")
            raise RuntimeError(f"音频提取失败: {result.stderr[-200:]}")

    # 计算时长
    import wave
    with wave.open(output_path, "r") as wf:
        duration = wf.getnframes() / wf.getframerate()

    print(f"提取完成: {output_path}")
    print(f"时长: {duration:.1f} 秒 | 格式: 16kHz 单声道 WAV")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python extract_audio.py <文件路径> [输出路径]")
        print("示例: python extract_audio.py 稿子.mp3 models/my_voice_ref.wav")
        sys.exit(1)

    video = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.isfile(video):
        print(f"文件不存在: {video}")
        sys.exit(1)

    extract_audio(video, out)
