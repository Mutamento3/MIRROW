"""Conservative, host-local GPU STT pilot for ambient clips only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

MODEL_LABEL = "large-v3-turbo-q5_0"
MIN_START_INTERVAL_SECONDS = 20
TIMEOUT_SECONDS = 30
_DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "models" / "ambient-gpu"


def enabled() -> bool:
    from settings_manager import get_setting
    return get_setting("ambient_gpu_pilot_enabled") is True


def _assets() -> tuple[Path, Path]:
    root = Path(os.environ.get("MIRROW_AMBIENT_GPU_PILOT_DIR") or _DEFAULT_ROOT)
    return (root / "whisper-build" / "bin" / "Release" / "whisper-cli.exe",
            root / "ggml-large-v3-turbo-q5_0.bin")


def ready() -> bool:
    cli, model = _assets()
    return cli.is_file() and model.is_file()


def transcribe(path: Path) -> str:
    cli, model = _assets()
    if not cli.is_file() or not model.is_file():
        raise FileNotFoundError("ambient_gpu_pilot_assets_missing")
    result = subprocess.run(
        [str(cli), "-m", str(model), "-f", str(path), "-l", "zh",
         "-t", "1", "-bs", "5", "-np", "-nt"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_SECONDS, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("ambient_gpu_pilot_transcription_failed")
    return result.stdout.strip()
