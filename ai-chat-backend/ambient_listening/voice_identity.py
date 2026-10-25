"""Local, manually enrolled speaker evidence; never an ASR or identity authority."""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import sqlite3
import threading
import time
import uuid
import wave
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "voice_setup" / "speaker" / "campplus_zh.onnx"
# Official sherpa-onnx speaker-recongition-models release, 3D-Speaker Chinese CAM++.
MODEL_SHA256 = "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"
MODEL_ID = "3dspeaker-campplus-zh-16k-f682b514-v1"
THRESHOLD = 0.70  # cosine similarity, not a probability; validate on Human's samples
MARGIN = 0.10


def unknown(reason: str) -> dict:
    return {"entity_id": "unknown", "basis": reason, "confidence": 0.0}


def read_wav(data: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise ValueError("需要 16kHz 单声道 PCM16 WAV")
            if not 0 < wav.getnframes() <= 16000 * 40:
                raise ValueError("录音需在 40 秒以内")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
        return samples
    except (wave.Error, EOFError) as exc:
        raise ValueError("无法读取 WAV 录音") from exc


def windows(samples: np.ndarray) -> list[np.ndarray]:
    """Cover the clip in short windows; energy only rejects silence, not music."""
    result = []
    for offset in range(0, len(samples), 16000 * 4):
        chunk = samples[offset:offset + 16000 * 4]
        # Join a short tail to its predecessor rather than inventing its identity.
        if len(chunk) < 16000 * 1.5:
            if result:
                result[-1] = np.concatenate((result[-1], chunk))
            continue
        if float(np.sqrt(np.mean(chunk ** 2))) >= 0.006:
            result.append(chunk)
    return result


def normalized(vector) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(value))
    if value.ndim != 1 or not np.isfinite(value).all() or norm < 1e-8:
        raise ValueError("声纹向量无效")
    return value / norm


def match(vector: np.ndarray, profiles: list[dict]) -> dict:
    # Compare each entity's best sample; two microphones must not compete as people.
    by_entity = {}
    for profile in profiles:
        reference = normalized(json.loads(profile["embedding_json"]))
        if len(reference) != len(vector):
            continue
        score = float(vector @ reference)
        old = by_entity.get(profile["entity_id"])
        if old is None or score > old[0]:
            by_entity[profile["entity_id"]] = (score, profile)
    ranked = sorted(by_entity.values(), key=lambda item: item[0], reverse=True)
    if not ranked:
        return unknown("no_enrolled_voice")
    score, profile = ranked[0]
    if score < THRESHOLD or (len(ranked) > 1 and score - ranked[1][0] < MARGIN):
        return {**unknown("voiceprint_below_threshold_or_ambiguous"), "similarity": round(score, 3)}
    return {"entity_id": profile["entity_id"], "name": profile["label"],
            "basis": "local_voiceprint_candidate", "similarity": round(score, 3),
            "sample_id": profile["sample_id"], "identity_is_inferred": True}


def combine(speakers: list[dict]) -> dict:
    ids = {item.get("entity_id", "unknown") for item in speakers}
    if len(ids) != 1 or not ids or "unknown" in ids:
        return unknown("mixed_or_unconfirmed_speakers")
    result = dict(speakers[0])
    result["similarity"] = min(float(item.get("similarity", 0)) for item in speakers)
    result["basis"] = "local_voiceprint_candidate"
    result["identity_is_inferred"] = True
    return result


class VoiceIdentity:
    def __init__(self, db_path: Path | None = None, model_path: Path = MODEL_PATH):
        self.db_path = db_path or ROOT / "data" / "voice_identity.db"
        self.model_path = model_path
        self._lock = threading.RLock()
        self._extractor = None
        self._drafts: dict[str, dict] = {}
        self._error = ""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS voice_samples (sample_id TEXT PRIMARY KEY, "
                         "entity_id TEXT NOT NULL, label TEXT NOT NULL, source TEXT NOT NULL, "
                         "model_id TEXT NOT NULL, embedding_json TEXT NOT NULL, created_at TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def profiles(self, private=False) -> list[dict]:
        columns = "*" if private else "sample_id,entity_id,label,source,model_id,created_at"
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                f"SELECT {columns} FROM voice_samples WHERE model_id=? ORDER BY created_at", (MODEL_ID,))]

    def status(self) -> dict:
        available = (self.model_path.is_file() and self.model_path.stat().st_size >= 28000000
                     and importlib.util.find_spec("sherpa_onnx") is not None and not self._error)
        return {"available": available, "reason": self._error or ("ready" if available else "local_voice_identity_model_not_installed"),
                "model": MODEL_ID, "provider": "cpu", "num_threads": 1,
                "threshold": THRESHOLD, "samples": self.profiles(),
                "note": "相似度不是身份概率；按片段提供候选，不做逐句多人分离"}

    def _embedding(self, samples: np.ndarray) -> np.ndarray:
        if self._extractor is None:
            if hashlib.sha256(self.model_path.read_bytes()).hexdigest() != MODEL_SHA256:
                raise RuntimeError("voice_identity_model_version_mismatch")
            import sherpa_onnx
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path), num_threads=1, provider="cpu", debug=False)
            if not config.validate():
                raise RuntimeError("voice_identity_model_invalid")
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=np.ascontiguousarray(samples))
        stream.input_finished()
        if not self._extractor.is_ready(stream):
            raise ValueError("有效讲话太短，请多说几句")
        return normalized(self._extractor.compute(stream))

    def prepare(self, data: bytes, source: str) -> dict:
        samples = read_wav(data)
        if not 8 <= len(samples) / 16000 <= 35:
            raise ValueError("请录 8 至 35 秒的自然讲话，建议 15 至 25 秒")
        chunks = windows(samples)
        if len(chunks) < 2:
            raise ValueError("有效声音太少或音量太低，请靠近麦克风重录")
        if float(np.mean(np.abs(samples) >= 0.98)) > 0.03:
            raise ValueError("录音明显爆音，请离麦克风稍远后重录")
        with self._lock:
            vectors = [self._embedding(chunk) for chunk in chunks]
            consistency = min(float(a @ b) for i, a in enumerate(vectors) for b in vectors[i+1:])
            if consistency < 0.50:
                raise ValueError("各段声音差异较大，请确保只有本人讲话、没有音乐后重录")
            now = time.monotonic()
            self._drafts = {key: value for key, value in self._drafts.items() if value["expiry"] > now}
            if len(self._drafts) >= 8:
                raise ValueError("待确认录音过多，请先确认或等待十分钟后重试")
            draft_id = uuid.uuid4().hex
            self._drafts[draft_id] = {"vector": normalized(np.mean(vectors, axis=0)),
                                      "source": source[:100], "expiry": now + 600}
        return {"draft_id": draft_id, "duration_seconds": round(len(samples) / 16000, 1),
                "consistency": round(consistency, 3), "source": source[:100],
                "note": "尚未绑定。请本人确认录音归属；原始录音不在主机持久保存。"}

    def confirm(self, draft_id: str, entity_id: str, label: str) -> dict:
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT entity_id FROM voice_samples WHERE sample_id=?", (draft_id,)).fetchone()
            if existing:
                if existing[0] != entity_id:
                    raise ValueError("此录音已绑定其他人物，请重新录入")
                return {"sample_id": draft_id, "entity_id": entity_id, "already_confirmed": True}
            draft = self._drafts.get(draft_id)
            if not draft or draft["expiry"] <= time.monotonic():
                raise ValueError("待确认录音已过期或服务已重启，请重录")
            if conn.execute("SELECT COUNT(*) FROM voice_samples WHERE entity_id=?", (entity_id,)).fetchone()[0] >= 8:
                raise ValueError("该人物已有八份样本，请先删除不需要的样本")
            conn.execute("INSERT INTO voice_samples VALUES(?,?,?,?,?,?,?)", (
                draft_id, entity_id, label[:80], draft["source"], MODEL_ID,
                json.dumps(draft["vector"].tolist()), datetime.now(timezone.utc).isoformat()))
            self._drafts.pop(draft_id, None)
        return {"sample_id": draft_id, "entity_id": entity_id, "label": label[:80]}

    def delete(self, sample_id: str) -> bool:
        with self._lock, self._connect() as conn:
            return bool(conn.execute("DELETE FROM voice_samples WHERE sample_id=?", (sample_id,)).rowcount)

    def identify(self, path: Path) -> dict:
        start = time.monotonic()
        profiles = self.profiles(private=True)
        if not profiles:
            return unknown("no_enrolled_voice")
        if not self.status()["available"]:
            return unknown("voice_identity_model_unavailable")
        try:
            chunks = windows(read_wav(path.read_bytes()))
            if not chunks:
                return unknown("voice_too_short_or_quiet")
            with self._lock:
                observations = [match(self._embedding(chunk), profiles) for chunk in chunks]
            result = combine(observations)
            result["window_candidates"] = [{key: value for key, value in item.items()
                                           if key in {"entity_id", "name", "basis", "similarity"}}
                                          for item in observations]
            result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            return result
        except Exception:
            # Identity failure must never discard otherwise usable transcription.
            return unknown("voice_identity_analysis_failed")


_instance = None
_instance_lock = threading.Lock()


def get_voice_identity() -> VoiceIdentity:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = VoiceIdentity()
    return _instance
