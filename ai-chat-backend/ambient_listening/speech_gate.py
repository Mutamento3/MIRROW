"""Conservative short-clip preflight; uncertainty passes, raw audio is untouched."""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np

MAX_FILTER_SECONDS = 3.0
NO_SPEECH_THRESHOLD = 0.10
_lock = threading.Lock()


def inspect_speech(path: Path) -> dict:
    started = time.perf_counter()
    result = {"status": "unavailable", "skip_stt": False}
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise ValueError("unsupported_wav")
            duration = wav.getnframes() / 16000
            result["duration_seconds"] = round(duration, 3)
            # Do not load/run a model for long clips or truncate their speech.
            if duration > MAX_FILTER_SECONDS or duration < 0.25:
                result.update(status="not_filtered", basis="duration_outside_short_clip_gate")
                return result
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
        # Already bundled with the existing STT dependency; CPU-only, one thread.
        # SileroVADModel resets recurrent state for every call.
        from faster_whisper.vad import get_vad_model
        with _lock:
            probabilities = np.asarray(get_vad_model()(np.pad(audio, (0, (-len(audio)) % 512))))
        peak = float(probabilities.max())
        if not np.isfinite(peak):
            raise ValueError("invalid_vad_output")
        skip = peak < NO_SPEECH_THRESHOLD
        result.update(status="no_speech_detected" if skip else "speech_possible",
                      skip_stt=skip, peak_score=round(peak, 4),
                      basis="silero_v6_short_clip_peak", threshold=NO_SPEECH_THRESHOLD)
    except Exception as exc:
        # Missing/broken detector never discards potentially useful speech.
        result.update(status="unavailable", skip_stt=False, error_code=type(exc).__name__)
    finally:
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result
