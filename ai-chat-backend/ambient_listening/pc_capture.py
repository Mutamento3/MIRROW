"""Windows microphone capture for the existing ambient-material pipeline.

Only an explicitly selected input device is opened.  Output/loopback endpoints
are never candidates, and no audio is written here: the ambient store remains
the sole authority for the 24-hour recording lifecycle.
"""

from __future__ import annotations

import io
import math
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320
SPEECH_THRESHOLD = 520
START_FRAMES = 3
SILENCE_FRAMES = 60
MIN_SEGMENT_FRAMES = 30
MAX_SEGMENT_FRAMES = 1500
_scan_lock = threading.Lock()
_last_scan = 0.0
_cached_microphones: list[Microphone] = []


@dataclass(frozen=True)
class Microphone:
    index: int
    name: str
    sample_rate: int


def microphones() -> list[Microphone]:
    global _last_scan, _cached_microphones
    with _scan_lock:
        if time.monotonic() - _last_scan < 10:
            return list(_cached_microphones)
        try:
            import sounddevice as sd
            apis = sd.query_hostapis()
            _cached_microphones = [
                Microphone(index, str(device["name"]), int(device["default_samplerate"]))
                for index, device in enumerate(sd.query_devices())
                if device["max_input_channels"] >= 1
                and "WASAPI" in str(apis[device["hostapi"]]["name"]).upper()
            ]
        except Exception:
            _cached_microphones = []
        _last_scan = time.monotonic()
        return list(_cached_microphones)


def selected_microphone(name_part: str) -> Microphone | None:
    name_part = str(name_part or "").strip().casefold()
    if not name_part:
        return None
    return next((device for device in microphones()
                 if name_part in device.name.casefold()), None)


def _wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return output.getvalue()


def _frame_at_16k(raw: bytes, sample_rate: int) -> bytes:
    if sample_rate == SAMPLE_RATE:
        return raw
    from scipy.signal import resample_poly
    samples = np.frombuffer(raw, dtype="<i2")
    divisor = math.gcd(SAMPLE_RATE, sample_rate)
    converted = resample_poly(samples, SAMPLE_RATE // divisor, sample_rate // divisor)
    return np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()


class PcAmbientRecorder:
    def __init__(self, on_segment: Callable[[bytes, int, str], None]):
        self._on_segment = on_segment
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.running = False
        self.error = ""
        self.device_name = ""
        self.overflows = 0
        self._retry_after = 0.0

    def start(self, microphone: Microphone) -> bool:
        if self.running and self.device_name == microphone.name:
            return True
        self.stop()
        if time.monotonic() < self._retry_after:
            return False
        self.error = ""
        self.overflows = 0
        self.device_name = microphone.name
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, args=(microphone,),
                                        name="ambient-pc-microphone", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        if not self.running:
            self.stop()
        return self.running

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(1.0)
        self.running = False
        self._thread = None

    def _run(self, microphone: Microphone) -> None:
        try:
            import sounddevice as sd
            sample_rate = microphone.sample_rate
            frames_per_read = round(sample_rate / 50)
            with sd.RawInputStream(device=microphone.index, samplerate=sample_rate,
                                   channels=1, dtype="int16", blocksize=frames_per_read) as stream:
                self.running = True
                self._ready.set()
                preroll: deque[bytes] = deque(maxlen=15)
                segment: list[bytes] | None = None
                speech_frames = silence_frames = total_frames = 0
                started_at_ms = 0
                last_nonzero = time.monotonic()
                while not self._stop.is_set():
                    raw, overflow = stream.read(frames_per_read)
                    if overflow:
                        self.overflows += 1
                        if self.overflows >= 5:
                            raise RuntimeError("pc_mic_overflow")
                    frame = _frame_at_16k(bytes(raw), sample_rate)
                    if len(frame) != FRAME_SAMPLES * 2:
                        raise RuntimeError("pc_mic_frame_size_invalid")
                    samples = np.frombuffer(frame, dtype="<i2").astype(np.int32)
                    energy = int(np.mean(np.abs(samples)))
                    if energy > 20:
                        last_nonzero = time.monotonic()
                    elif time.monotonic() - last_nonzero > 30:
                        raise RuntimeError("pc_mic_silent")
                    speech = energy >= SPEECH_THRESHOLD
                    if segment is None:
                        preroll.append(frame)
                        speech_frames = speech_frames + 1 if speech else 0
                        if speech_frames >= START_FRAMES:
                            segment = list(preroll)
                            total_frames = len(segment)
                            silence_frames = 0
                            started_at_ms = int(time.time() * 1000)
                            preroll.clear()
                        continue
                    segment.append(frame)
                    total_frames += 1
                    silence_frames = 0 if speech else silence_frames + 1
                    if ((silence_frames >= SILENCE_FRAMES and total_frames >= MIN_SEGMENT_FRAMES)
                            or total_frames >= MAX_SEGMENT_FRAMES):
                        self._on_segment(_wav(b"".join(segment)), started_at_ms,
                                         microphone.name)
                        segment = None
                        speech_frames = silence_frames = total_frames = 0
        except Exception as exc:
            self.error = (str(exc) if str(exc) in {"pc_mic_silent", "pc_mic_overflow"}
                          else type(exc).__name__)
            self._retry_after = time.monotonic() + 60
        finally:
            self.running = False
            self._ready.set()
