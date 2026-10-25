from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import time
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .policy import (MAX_ANALYSIS_LAG_SECONDS, MAX_CONTACT_MATERIAL_AGE_SECONDS,
                     capture_decision, contact_decision, effective_listening_thresholds,
                     listening_thresholds, resolved_schedule)
from .store import get_ambient_store
from .voice_identity import get_voice_identity, combine, unknown

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_MATERIAL_AGE_SECONDS = 24 * 3600
DICTATION_LOOKBACK_SECONDS = 150
DICTATION_GRACE_SECONDS = 90


class AmbientListeningService:
    def __init__(self):
        self.store = get_ambient_store()
        self._task: asyncio.Task | None = None
        self._source_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._evaluate_lock = asyncio.Lock()
        self._source_lock = asyncio.Lock()
        self._last_evaluation_at = 0.0
        self._last_gpu_start_at = 0.0
        self._capture_source = "phone"
        self._source_reason = "pc_microphone_not_selected"
        self._pc_selected_since_ms = 0
        self._recent_pc_intervals: list[tuple[int, int]] = []
        self._last_source_config_sent_at = 0.0
        self._last_input_diagnostic_at = 0.0
        self._pc_recorder = None
        self._event_writer: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None
        self._message_generator: Callable[[list[dict[str, Any]], bool], Awaitable[Any]] | None = None
        self._message_sender: Callable[[dict[str, Any]], Awaitable[bool]] | None = None
        self._note_sender: Callable[[str, str, str], Awaitable[bool]] | None = None
        self._session_provider: Callable[[], str] | None = None
        self._recent_user_messages_provider: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None

    def configure(self, *, event_writer=None, message_generator=None,
                  message_sender=None, note_sender=None, session_provider=None,
                  recent_user_messages_provider=None) -> None:
        self._event_writer = event_writer
        self._message_generator = message_generator
        self._message_sender = message_sender
        self._note_sender = note_sender
        self._session_provider = session_provider
        self._recent_user_messages_provider = recent_user_messages_provider

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.store.cleanup_expired()
        self._stop.clear()
        self._source_task = asyncio.create_task(self._source_loop(), name="ambient-source-routing")
        self._task = asyncio.create_task(self._run(), name="ambient-listening")

    async def stop(self) -> None:
        self._stop.set()
        if self._source_task:
            try:
                await asyncio.wait_for(self._source_task, timeout=3.0)
            except asyncio.TimeoutError:
                self._source_task.cancel()
                try:
                    await self._source_task
                except asyncio.CancelledError:
                    pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._source_task = None
        if self._pc_recorder is not None:
            await asyncio.to_thread(self._pc_recorder.stop)

    def source_status(self) -> dict[str, Any]:
        from .source_routing import configured_microphone, input_diagnostics
        return {
            "selected": self._capture_source,
            "reason": self._source_reason,
            "pc_microphone": (self._pc_recorder.device_name if self._pc_recorder
                              and self._pc_recorder.running else ""),
            "pc_running": bool(self._pc_recorder and self._pc_recorder.running),
            "pc_error": self._pc_recorder.error if self._pc_recorder else "",
            "pc_overflows": self._pc_recorder.overflows if self._pc_recorder else 0,
            "input": input_diagnostics(),
            "configured_microphone": configured_microphone(),
        }

    def mobile_config(self) -> dict[str, Any]:
        decision = capture_decision()
        schedule: dict[str, Any] = {}
        rest_day = False
        private_at_ms = 0
        privacy_windows: list[Any] = []
        schedule = resolved_schedule()
        try:
            from calendar_manager.database import is_rest_day_today
            rest_day = bool(is_rest_day_today())
        except Exception:
            pass
        try:
            from shared_state import get_last_private_chat_time
            private_at = get_last_private_chat_time(resolve_from_store=True)
            if private_at:
                private_at_ms = int(private_at.timestamp() * 1000)
        except Exception:
            pass
        try:
            from settings_manager import get_setting
            value = get_setting("ambient_listening_privacy_windows") or []
            privacy_windows = value if isinstance(value, list) else []
        except Exception:
            pass
        return {
            "type": "ambient_listening_config",
            "protocol": 1,
            "mode": decision.mode,
            "capture_allowed": decision.allowed,
            "reason": decision.reason,
            "behavior_mode": decision.behavior_mode,
            "work_start": str(schedule.get("work_start") or ""),
            "work_end": str(schedule.get("work_end") or ""),
            "calendar_rest_day": rest_day,
            "policy_local_date": datetime.now().strftime("%Y-%m-%d"),
            "last_private_chat_at_ms": private_at_ms,
            "privacy_windows": privacy_windows,
            "idle_threshold_seconds": listening_thresholds()["idle_minutes"] * 60,
            "max_local_age_seconds": MAX_MATERIAL_AGE_SECONDS,
            "max_segment_seconds": 30,
            "server_time_ms": int(time.time() * 1000),
            "selected_source": "pc" if self._capture_source == "pc" else "phone",
            # A crashed/offline host cannot leave the phone microphone paused.
            "source_lease_until_ms": (int(time.time() * 1000) + 30_000
                                      if self._capture_source == "pc" else 0),
        }

    @staticmethod
    def _safe_id(value: Any, prefix: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value or ""))[:96]
        return value or f"{prefix}_{uuid.uuid4().hex}"

    @staticmethod
    def _captured_time(value: Any) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000.0, timezone.utc)
        text = str(value or "").strip()
        if text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    @classmethod
    def _material_too_old(cls, rows: list[dict[str, Any]]) -> bool:
        if not rows:
            return True
        try:
            oldest = min(cls._captured_time(row.get("captured_at")) for row in rows)
            return (datetime.now(timezone.utc) - oldest).total_seconds() > MAX_CONTACT_MATERIAL_AGE_SECONDS
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _audio_context(value: Any) -> dict[str, Any]:
        value = value if isinstance(value, dict) else {}
        compact = lambda key, cap: str(value.get(key) or "").replace("\n", " ")[:cap]
        try:
            observed_at_ms = int(value.get("observed_at_ms") or 0)
        except (TypeError, ValueError):
            observed_at_ms = 0
        return {
            "kind": compact("kind", 32),
            "capture_source": compact("capture_source", 16),
            "capture_device": compact("capture_device", 80),
            "media_playback_active": bool(value.get("media_playback_active")),
            "audio_mode": compact("audio_mode", 32),
            "source_app_package": compact("source_app_package", 160),
            "source_app_name": compact("source_app_name", 80),
            "source_basis": compact("source_basis", 48),
            "source_confidence": compact("source_confidence", 24),
            "media_source_app_package": compact("media_source_app_package", 160),
            "media_source_app_name": compact("media_source_app_name", 80),
            "media_source_basis": compact("media_source_basis", 48),
            "call_source_app_package": compact("call_source_app_package", 160),
            "call_source_app_name": compact("call_source_app_name", 80),
            "call_source_basis": compact("call_source_basis", 48),
            "input_method_visible": bool(value.get("input_method_visible")),
            "input_method_app_package": compact("input_method_app_package", 160),
            "input_method_app_name": compact("input_method_app_name", 80),
            "input_method_basis": compact("input_method_basis", 48),
            "screen_on": bool(value.get("screen_on")),
            "observed_at_ms": observed_at_ms,
            "aec_enabled": bool(value.get("aec_enabled")),
        }

    @classmethod
    def _material_contexts(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            try:
                raw = json.loads(row.get("audio_context_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            context = cls._audio_context(raw)
            relation = str(row.get("message_relation") or "")[:48]
            if relation:
                context["message_relation"] = relation
                context["linked_message_id"] = str(row.get("linked_message_id") or "")[:160]
                try:
                    context["relation_score"] = round(float(row.get("relation_score") or 0.0), 4)
                except (TypeError, ValueError):
                    context["relation_score"] = 0.0
            if not any((context["kind"], context["capture_source"], context["source_app_package"],
                        context["media_source_app_package"], context["call_source_app_package"],
                        context["input_method_app_package"], relation,
                        context["media_playback_active"], context["audio_mode"])):
                continue
            key = json.dumps(context, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            contexts.append(context)
            if len(contexts) >= 12:
                break
        return contexts

    @staticmethod
    def _match_text(value: Any) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())

    @staticmethod
    def _row_audio_context(row: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(row.get("audio_context_json") or "{}")
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @classmethod
    def _message_echo_match(cls, rows: list[dict[str, Any]], content: str,
                            accepted_at: datetime, authoritative_voice: bool = False
                            ) -> dict[str, Any] | None:
        message = cls._match_text(content)
        if len(message) < 6:
            return None
        selected: list[dict[str, Any]] = []
        has_input_method = False
        for row in rows:
            try:
                captured = cls._captured_time(row.get("captured_at"))
            except (TypeError, ValueError):
                continue
            delta = (accepted_at - captured).total_seconds()
            if delta < -10 or delta > DICTATION_LOOKBACK_SECONDS:
                continue
            segment = cls._match_text(row.get("transcript"))
            if len(segment) < 4:
                continue
            matcher = SequenceMatcher(None, segment, message, autojunk=False)
            longest = matcher.find_longest_match(0, len(segment), 0, len(message)).size
            segment_coverage = longest / max(1, len(segment))
            score = max(matcher.ratio(), segment_coverage)
            if longest < 4 or score < 0.72:
                continue
            selected.append({"row": row, "segment": segment, "score": score})
            has_input_method = has_input_method or bool(
                cls._row_audio_context(row).get("input_method_visible"))
        if not selected:
            return None
        combined = "".join(item["segment"] for item in selected)
        matcher = SequenceMatcher(None, combined, message, autojunk=False)
        longest = matcher.find_longest_match(0, len(combined), 0, len(message)).size
        score = max(matcher.ratio(), longest / max(1, min(len(combined), len(message))))
        message_coverage = longest / max(1, len(message))
        enough_body = matcher.ratio() >= 0.74 or (score >= 0.86 and message_coverage >= 0.55)
        confirmed = bool((has_input_method or authoritative_voice) and enough_body)
        possible = bool(score >= 0.78 and message_coverage >= 0.45)
        if not confirmed and not possible:
            return None
        return {
            "clip_ids": [item["row"]["clip_id"] for item in selected],
            "score": score,
            "confirmed": confirmed,
            "input_method_visible": has_input_method,
        }

    async def observe_user_message(self, content: str, message_id: str, *,
                                   accepted_at: str | datetime | None = None,
                                   authoritative_voice: bool = False) -> dict[str, Any]:
        """Reconcile an authoritative private message with recent ambient material."""
        if not str(content or "").strip() or not str(message_id or "").strip():
            return {"matched": 0, "confirmed": False}
        when = self._captured_time(accepted_at or datetime.now(timezone.utc).isoformat())
        rows = await asyncio.to_thread(self.store.ready_material, 80, MAX_CONTACT_MATERIAL_AGE_SECONDS)
        match = self._message_echo_match(rows, content, when, authoritative_voice)
        if not match:
            return {"matched": 0, "confirmed": False}
        relation = "voice_dictation_duplicate" if match["confirmed"] else "possible_message_echo"
        changed = await asyncio.to_thread(
            self.store.mark_message_relation,
            match["clip_ids"],
            relation=relation,
            message_id=str(message_id),
            score=float(match["score"]),
            consume=bool(match["confirmed"]),
        )
        if changed:
            self.store.checkpoint(
                str(message_id), "message_echo_reconciliation",
                "success" if match["confirmed"] else "partial",
                relation, False,
                "exclude_duplicate_keep_raw_audio" if match["confirmed"] else "pass_context_to_flash",
            )
        return {"matched": changed, "confirmed": bool(match["confirmed"]),
                "relation": relation, "score": float(match["score"])}

    async def _reconcile_recent_user_messages(self) -> int:
        if self._recent_user_messages_provider is None:
            return 0
        rows = await asyncio.to_thread(self.store.ready_material, 80, MAX_CONTACT_MATERIAL_AGE_SECONDS)
        if not any(self._row_audio_context(row).get("input_method_visible") for row in rows):
            return 0
        try:
            messages = await self._recent_user_messages_provider()
        except Exception:
            logger.debug("ambient recent-user-message lookup failed", exc_info=True)
            return 0
        changed = 0
        for message in (messages or [])[-12:]:
            if str(message.get("role") or "") != "user":
                continue
            result = await self.observe_user_message(
                str(message.get("content") or ""),
                str(message.get("message_id") or ""),
                accepted_at=message.get("timestamp"),
                authoritative_voice=bool(message.get("voice_attachment")),
            )
            changed += int(result.get("matched") or 0)
        return changed

    @classmethod
    def _dictation_grace_active(cls, rows: list[dict[str, Any]]) -> bool:
        now = datetime.now(timezone.utc)
        for row in rows:
            if not cls._row_audio_context(row).get("input_method_visible"):
                continue
            try:
                age = (now - cls._captured_time(row.get("captured_at"))).total_seconds()
            except (TypeError, ValueError):
                continue
            if 0 <= age < DICTATION_GRACE_SECONDS:
                return True
        return False

    async def receive_segment(self, message: dict[str, Any]) -> dict[str, Any]:
        clip_id = self._safe_id(message.get("clip_id"), "clip")
        correlation_id = self._safe_id(message.get("correlation_id") or clip_id, "corr")
        decision = capture_decision()
        if not decision.allowed:
            self.store.checkpoint(correlation_id, "host_privacy_gate", "blocked",
                                  decision.reason, False, "discard_local")
            return {"type": "ambient_audio_ack", "clip_id": clip_id,
                    "accepted": False, "terminal": True, "delete_local": True,
                    "error_code": decision.reason}
        try:
            captured = self._captured_time(message.get("captured_at_ms") or message.get("captured_at"))
            expires = captured + timedelta(seconds=MAX_MATERIAL_AGE_SECONDS)
            now = datetime.now(timezone.utc)
            if expires <= now:
                self.store.checkpoint(correlation_id, "host_receive", "blocked",
                                      "expired_before_receipt", False, "discard_local")
                return {"type": "ambient_audio_ack", "clip_id": clip_id,
                        "accepted": False, "terminal": True, "delete_local": True,
                        "error_code": "expired_before_receipt"}
            raw = base64.b64decode(str(message.get("audio_b64") or ""), validate=True)
            if len(raw) < 44 or len(raw) > MAX_AUDIO_BYTES or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
                raise ValueError("invalid_wav")
            captured_ms = int(captured.timestamp() * 1000)
            pc_overlap = (self._capture_source == "pc" and self._pc_selected_since_ms
                          and captured_ms >= self._pc_selected_since_ms)
            pc_overlap = pc_overlap or any(
                start <= captured_ms <= end for start, end in self._recent_pc_intervals)
            if pc_overlap:
                self.store.checkpoint(correlation_id, "source_gate", "blocked",
                                      "pc_microphone_selected", False, "discard_phone_overlap")
                return {"type": "ambient_audio_ack", "clip_id": clip_id,
                        "accepted": False, "terminal": True, "delete_local": True,
                        "error_code": "pc_microphone_selected"}
            audio_context = self._audio_context(message.get("audio_context"))
            audio_context["capture_source"] = "phone"
            created, _ = await asyncio.to_thread(
                self.store.save_segment,
                clip_id=clip_id, correlation_id=correlation_id,
                captured_at=captured.isoformat(), expires_at=expires.isoformat(),
                audio_bytes=raw,
                audio_context=audio_context,
            )
            self.store.checkpoint(correlation_id, "host_receipt", "success",
                                  "", False, "queued_for_local_analysis")
            return {"type": "ambient_audio_ack", "clip_id": clip_id,
                    "accepted": True, "terminal": True, "delete_local": True,
                    "duplicate": not created}
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            self.store.checkpoint(correlation_id, "host_receive", "fault", code,
                                  False, "discard_invalid_segment")
            return {"type": "ambient_audio_ack", "clip_id": clip_id,
                    "accepted": False, "terminal": True, "delete_local": True,
                    "error_code": code[:80]}

    def record_mobile_checkpoint(self, message: dict[str, Any]) -> None:
        self.store.checkpoint(
            self._safe_id(message.get("correlation_id"), "mobile"),
            f"mobile_{str(message.get('stage') or 'unknown')[:32]}",
            str(message.get("status") or "unknown")[:24],
            str(message.get("error_code") or "")[:80],
            bool(message.get("retryable")),
            str(message.get("next_action") or "")[:120],
        )

    async def _run(self) -> None:
        last_cleanup_at = time.monotonic()
        while not self._stop.is_set():
            try:
                from . import gpu_pilot
                gpu_enabled = gpu_pilot.enabled()
                # Keep STT sequential and do not spend CPU on ambient prosody
                # that the current decision does not consume. In the GPU pilot,
                # leave a gap between starts so a game gets breathing room.
                # Under sustained phone audio, favor current speech over an
                # older queued clip. The unselected clips keep their normal
                # 24-hour audio retention but age out of participation.
                may_start = capture_decision().allowed and (
                    not gpu_enabled or time.monotonic() - self._last_gpu_start_at
                    >= gpu_pilot.MIN_START_INTERVAL_SECONDS)
                pending = (await asyncio.to_thread(
                    self.store.pending_segments, 1, MAX_ANALYSIS_LAG_SECONDS, True
                )) if may_start else []
                for segment in pending:
                    await self._analyse(segment)
                await self._maybe_evaluate()
                if time.monotonic() - last_cleanup_at >= 60:
                    await asyncio.to_thread(self.store.cleanup_expired)
                    last_cleanup_at = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("ambient listening worker iteration failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _source_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._sync_capture_source()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("ambient source routing iteration failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def _receive_pc_segment(self, wav: bytes, captured_at_ms: int, device_name: str) -> None:
        if self._capture_source != "pc" or not capture_decision().allowed:
            return
        clip_id = f"pc_{uuid.uuid4().hex}"
        try:
            captured = self._captured_time(captured_at_ms)
            self.store.save_segment(
                clip_id=clip_id, correlation_id=clip_id,
                captured_at=captured.isoformat(),
                expires_at=(captured + timedelta(seconds=MAX_MATERIAL_AGE_SECONDS)).isoformat(),
                audio_bytes=wav,
                audio_context={"kind": "pc_microphone", "capture_source": "pc",
                               "capture_device": device_name[:80],
                               "source_basis": "recent_local_physical_input",
                               "source_confidence": "inferred"},
            )
            self.store.checkpoint(clip_id, "pc_segment_receipt", "success", "", False,
                                  "queued_for_local_analysis")
        except Exception as exc:
            self.store.checkpoint(clip_id, "pc_segment_receipt", "fault",
                                  type(exc).__name__, False, "inspect_pc_microphone")

    async def _sync_capture_source(self) -> None:
        async with self._source_lock:
            await self._apply_capture_source()

    async def _apply_capture_source(self) -> None:
        decision = capture_decision()
        source = "paused" if not decision.allowed else "phone"
        reason = decision.reason if not decision.allowed else "pc_microphone_not_selected"
        microphone = None
        if decision.allowed:
            from .source_routing import computer_candidate
            microphone, reason = await asyncio.to_thread(
                computer_candidate, self._capture_source)
        if microphone is not None:
            if self._pc_recorder is None:
                from .pc_capture import PcAmbientRecorder
                self._pc_recorder = PcAmbientRecorder(self._receive_pc_segment)
            if await asyncio.to_thread(self._pc_recorder.start, microphone):
                source = "pc"
            else:
                reason = self._pc_recorder.error or "pc_microphone_start_failed"
        elif self._pc_recorder is not None and self._pc_recorder.running:
            await asyncio.to_thread(self._pc_recorder.stop)
        changed = source != self._capture_source or reason != self._source_reason
        if source != "pc" and self._capture_source == "pc" and self._pc_selected_since_ms:
            self._recent_pc_intervals.append((self._pc_selected_since_ms,
                                              int(time.time() * 1000)))
            self._recent_pc_intervals = self._recent_pc_intervals[-8:]
        cutoff_ms = int(time.time() * 1000) - MAX_CONTACT_MATERIAL_AGE_SECONDS * 1000
        self._recent_pc_intervals = [interval for interval in self._recent_pc_intervals
                                     if interval[1] >= cutoff_ms]
        if source == "pc" and self._capture_source != "pc":
            self._pc_selected_since_ms = int(time.time() * 1000)
        self._capture_source, self._source_reason = source, reason
        if changed:
            self.store.checkpoint("host-source", "source_selection", "success", reason,
                                  False, source)
        if decision.allowed and time.monotonic() - self._last_input_diagnostic_at >= 60:
            from .source_routing import input_diagnostics
            diagnostic = input_diagnostics()
            self._last_input_diagnostic_at = time.monotonic()
            self.store.checkpoint("host-input", "source_input_health", "partial"
                                  if source != "pc" else "success", reason, False,
                                  f"{diagnostic['health']} p={diagnostic['packets']} "
                                  f"u={diagnostic['unknown_packets']} e={diagnostic['parse_errors']} "
                                  f"idle={diagnostic['physical_idle_seconds']} "
                                  f"packet={diagnostic['packet_age_seconds']}")
        if changed or (source == "pc" and time.monotonic() - self._last_source_config_sent_at >= 10):
            self._last_source_config_sent_at = time.monotonic()
            try:
                from mobile_relay import get_mobile_relay_hub
                await get_mobile_relay_hub().send_payload(self.mobile_config())
            except Exception:
                logger.debug("ambient source config push unavailable", exc_info=True)

    async def _analyse(self, segment: dict[str, Any]) -> None:
        if not capture_decision().allowed:
            return
        from .speech_gate import inspect_speech
        gate = await asyncio.to_thread(inspect_speech, Path(segment["file_path"]))
        self.store.checkpoint(segment["correlation_id"], "speech_preflight",
                              "skipped" if gate["skip_stt"] else "partial"
                              if gate["status"] == "unavailable" else "success",
                              gate["status"], False,
                              "retain_audio_until_expiry" if gate["skip_stt"] else "local_stt")
        if gate["skip_stt"]:
            await asyncio.to_thread(self.store.skip_non_speech, segment["clip_id"], gate)
            return
        # Privacy/off can change while the preflight loads; do not start STT then.
        if not capture_decision().allowed:
            return
        transcript = await self._transcribe_segment(segment)
        if transcript is not None:
            await self._finish_analysis(segment, transcript, gate)

    async def _transcribe_segment(self, segment: dict[str, Any]) -> str | None:
        clip_id = segment["clip_id"]
        correlation_id = segment["correlation_id"]
        path = Path(segment["file_path"])
        try:
            from . import gpu_pilot
            if gpu_pilot.enabled():
                # Never fall back to the CPU large-v3 path during this pilot.
                self._last_gpu_start_at = time.monotonic()
                transcript = await asyncio.to_thread(gpu_pilot.transcribe, path)
            else:
                audio = await asyncio.to_thread(path.read_bytes)
                from voice_manager.stt_engine import transcribe
                transcript = await asyncio.to_thread(transcribe, audio)
            self.store.checkpoint(correlation_id, "local_stt", "success" if transcript else "partial",
                                  "" if transcript else "empty_transcript", False, "material")
            return transcript
        except Exception as exc:
            code = type(exc).__name__
            await asyncio.to_thread(self.store.fail_analysis, clip_id, code, False)
            self.store.checkpoint(correlation_id, "local_stt", "fault", code, False,
                                  "inspect_local_model")
            return None

    async def _finish_analysis(self, segment: dict[str, Any], transcript: str,
                               speech_gate: dict | None = None) -> None:
        clip_id = segment["clip_id"]
        correlation_id = segment["correlation_id"]
        try:
            self.store.checkpoint(correlation_id, "local_prosody", "skipped",
                                  "not_consumed_by_current_decision", False,
                                  "retain_audio_until_expiry")
            speaker = (await asyncio.to_thread(get_voice_identity().identify, Path(segment["file_path"]))
                       if transcript.strip() else unknown("empty_transcript"))
            self.store.checkpoint(correlation_id, "speaker_identity",
                                  "success" if speaker.get("entity_id") != "unknown" else "partial",
                                  str(speaker.get("basis") or ""), False, "material")
            await asyncio.to_thread(self.store.finish_analysis, clip_id, transcript,
                                    {"speaker": speaker, "speech_gate": speech_gate or {}})
            row = await asyncio.to_thread(self.store.get_segment, clip_id)
            if row and self._row_audio_context(row).get("input_method_visible"):
                await self._reconcile_recent_user_messages()
        except Exception as exc:
            code = type(exc).__name__
            await asyncio.to_thread(self.store.fail_analysis, clip_id, code, False)
            self.store.checkpoint(correlation_id, "material_ready", "fault", code, False,
                                  "inspect_local_analysis")

    async def _maybe_evaluate(self) -> None:
        thresholds = effective_listening_thresholds()
        cooldown = thresholds["cooldown_seconds"]
        if self._evaluate_lock.locked() or time.monotonic() - self._last_evaluation_at < cooldown:
            return
        await self._reconcile_recent_user_messages()
        rows = await asyncio.to_thread(self.store.ready_material, 80, MAX_CONTACT_MATERIAL_AGE_SECONDS)
        # A visible IME can mean ordinary typing or voice dictation. Give the
        # authoritative chat message a short chance to arrive; unmatched audio
        # remains eligible and is later passed to Flash with this provenance.
        if self._dictation_grace_active(rows):
            return
        text = "\n".join(str(row.get("transcript") or "").strip() for row in rows).strip()
        if not text:
            return
        first = self._captured_time(rows[0]["captured_at"])
        age = (datetime.now(timezone.utc) - first).total_seconds()
        if len(text) < thresholds["immediate_chars"] and not (
                len(text) >= thresholds["timed_min_chars"]
                and age >= thresholds["timed_seconds"]):
            return
        if self._material_too_old(rows):
            return
        decision = contact_decision()
        if not decision.allowed:
            return
        async with self._evaluate_lock:
            batch_id = f"ambient_{uuid.uuid4().hex}"
            decision_id = batch_id
            clip_ids = [row["clip_id"] for row in rows]
            await asyncio.to_thread(self.store.reserve, clip_ids, batch_id)
            correlation_id = rows[0]["correlation_id"]
            event_committed = False
            decision_recorded = False
            try:
                self.store.checkpoint(correlation_id, "material_threshold", "success")
                judgment = await self._judge(text, rows)
                if judgment is None:
                    # Transport/parse failure is not a semantic "do not contact".
                    # Keep the immutable snapshot eligible and retry after a short backoff.
                    await asyncio.to_thread(self.store.release, batch_id)
                    retry_delay = min(30, cooldown)
                    self._last_evaluation_at = time.monotonic() - (cooldown - retry_delay)
                    self.store.checkpoint(correlation_id, "flash_judgment", "fault",
                                          "judgment_unavailable", True, "retry_snapshot")
                    return
                if self._material_too_old(rows):
                    await asyncio.to_thread(self.store.release, batch_id)
                    self.store.checkpoint(correlation_id, "material_freshness", "blocked",
                                          "stale_after_judgment", False, "wait_for_fresh_material")
                    return
                self._last_evaluation_at = time.monotonic()
                await asyncio.to_thread(
                    self.store.record_decision,
                    decision_id=decision_id,
                    batch_id=batch_id,
                    correlation_id=correlation_id,
                    expires_at=max(str(row.get("expires_at") or "") for row in rows),
                    participate=bool(judgment.get("participate")),
                    event_description=str(judgment.get("event_description") or ""),
                    summary=str(judgment.get("summary") or ""),
                    reason=str(judgment.get("reason") or ""),
                    paper_note=str(judgment.get("paper_note") or ""),
                    material_count=len(rows),
                    material_chars=len(text),
                    device_background=self._material_contexts(rows),
                    outcome=("participate_requested" if judgment.get("participate")
                             else "no_participation"),
                )
                decision_recorded = True
                if not judgment.get("participate"):
                    await asyncio.to_thread(self.store.consume, batch_id)
                    self.store.checkpoint(correlation_id, "flash_judgment", "success",
                                          "no_participation", False, "wait_for_new_material")
                    return
                self.store.checkpoint(correlation_id, "flash_judgment", "success",
                                      "participate", False, "commit_observation_event")
                started_private_clock = decision.private_idle_seconds
                event_id = await self._commit_event(judgment, rows, batch_id)
                if not event_id:
                    raise RuntimeError("event_commit_failed")
                event_committed = True
                await asyncio.to_thread(
                    self.store.update_decision, decision_id,
                    outcome="event_committed", event_id=event_id,
                )
                self.store.checkpoint(correlation_id, "event_commit", "success")
                generated = await self._generate_message(event_id, judgment)
                if not generated:
                    await asyncio.to_thread(self.store.consume, batch_id)
                    await asyncio.to_thread(
                        self.store.update_decision, decision_id,
                        outcome="message_generation_failed", event_id=event_id,
                    )
                    self.store.checkpoint(correlation_id, "message_generation", "fault",
                                          "empty_message", True, "event_retained_retry_manually")
                    return
                self.store.checkpoint(correlation_id, "context_message_generation", "success")
                final = contact_decision()
                stale_at_final_gate = self._material_too_old(rows)
                # A real private reply during generation invalidates the uncommitted contact.
                if (stale_at_final_gate or not final.allowed or started_private_clock is None
                        or final.private_idle_seconds is None
                        or final.private_idle_seconds + 3 < started_private_clock):
                    await asyncio.to_thread(self.store.consume, batch_id)
                    await asyncio.to_thread(
                        self.store.update_decision, decision_id,
                        outcome=("message_cancelled_stale_material" if stale_at_final_gate
                                 else "message_cancelled_after_user_reply"), event_id=event_id,
                    )
                    self.store.checkpoint(correlation_id, "final_contact_gate", "blocked",
                                          "stale_material" if stale_at_final_gate else final.reason,
                                          False, "event_retained_message_cancelled")
                    return
                self.store.checkpoint(correlation_id, "final_contact_gate", "success")
                message_id = await self._send_message(event_id, generated, batch_id)
                if not message_id:
                    raise RuntimeError("message_commit_failed")
                await asyncio.to_thread(self.store.consume, batch_id)
                await asyncio.to_thread(
                    self.store.update_decision, decision_id,
                    outcome="message_sent", event_id=event_id, message_id=message_id,
                )
                self.store.checkpoint(correlation_id, "message_commit", "success")
                note = str(judgment.get("paper_note") or "").strip()[:80]
                if note and self._note_sender is not None:
                    session_id = self._session_provider() if self._session_provider else ""
                    try:
                        note_ok = await self._note_sender(note, batch_id, session_id)
                    except Exception:
                        note_ok = False
                    self.store.checkpoint(
                        correlation_id, "paper_note", "success" if note_ok else "partial",
                        "" if note_ok else "delivery_unconfirmed", False,
                        "receipt_persisted" if note_ok else "do_not_retry_automatically",
                    )
            except Exception as exc:
                if event_committed:
                    # E is already an immutable real event. Never regenerate a
                    # second E from the same audio just to repair a failed M.
                    await asyncio.to_thread(self.store.consume, batch_id)
                else:
                    await asyncio.to_thread(self.store.release, batch_id)
                if decision_recorded:
                    await asyncio.to_thread(
                        self.store.update_decision, decision_id,
                        outcome=("message_failed_event_retained" if event_committed
                                 else "evaluation_failed_retry_pending"),
                    )
                self.store.checkpoint(correlation_id, "evaluation", "fault",
                                      type(exc).__name__, not event_committed,
                                      "event_retained_no_auto_regeneration" if event_committed
                                      else "retry_snapshot")

    async def _judge(self, text: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        from wander_manager.flash_structured import call_flash_json_detailed
        device_background = self._material_contexts(rows)
        occurred_start = rows[0]["captured_at"] if rows else "unknown"
        occurred_end = rows[-1]["captured_at"] if rows else "unknown"
        learned_at = datetime.now(timezone.utc).isoformat()
        material_list = []
        remaining = 12000
        for row in rows:
            content = str(row.get("transcript") or "")[:remaining]
            if not content:
                continue
            remaining -= len(content)
            speaker = self._row_speaker(row)
            material_list.append({"captured_at": row.get("captured_at"), "text": content,
                                  "speaker": {key: speaker[key] for key in
                                              ("entity_id", "name", "basis", "similarity", "window_candidates") if key in speaker}})
        if not material_list and text:
            material_list.append({"text": text[:12000], "speaker": unknown("voice_identity_not_analysed")})
        prompt = (
            "以下是已授权环境监听在主机本地转写出的短期材料。它不是人类伙伴发给AI的消息，"
            "也不自动证明说话人身份。判断是否有足够具体、适合现在由AI主动联系的内容。"
            "材料按采集顺序列出，每条附本地声纹候选；local_voiceprint_candidate表示与手动登记声音相似，"
            "不是逐字发言认证，不证明整段只有此人。unknown保留为未知；混合片段尚未逐句分离。"
            "window_candidates按短声音窗口顺序给出局部候选，但没有逐句文本对齐，不能据此给具体句子强配人物。"
            "设备背景是与片段同时采集的事实或带依据的候选：active_media_session可确认媒体来源，"
            "音频模式in_call表示蜂窝通话，in_communication表示VoIP或视频通话；"
            "foreground_app_candidate和recent_app_before_lock_candidate都只是候选，不能当成确定来源。"
            "input_method_visible只证明输入法覆盖层当时可见，不单独证明正在语音听写；"
            "message_relation为possible_message_echo时，表示材料可能复述了随后发出的真实私聊，仍需结合背景判断。"
            "媒体播放时的歌词、通话中远端对方的声音都不能冒充人类伙伴的表达；无法拿准时保留不确定性，"
            "结合背景判断，而不是删除材料或强行归属。"
            "AI的主动联系也可以是自然的日常陪伴：游戏吐槽、生活近况、情绪和有趣的小事，"
            "不必出现求助、任务或明确后续行动才有参与价值。结合内容与设备背景判断现在是否适合接话。"
            "普通闲聊或说话人未知本身不是拒绝参与的理由；无法拿准的身份与音源保留背景和不确定性，"
            "由你综合判断联系价值，不把未知强行补成人类伙伴，也不为了提高参与率每批都联系。"
            "若参与，summary只概括听到的内容，不复制逐字转写，不推断长期立场。"
            "AI是接收这段声音并可能发消息的主体，不是音频里的说话人，也不是转写内容中的行动者。"
            "没有独立的说话人证据时，summary应写‘片段中有人提到……’，"
            "不得写‘AI正在……’、‘人类伙伴正在……’，也不能把前台应用候选写成声音来源。"
            "reason只说明为何AI考虑联系人类伙伴，不把片段里的任务归为AI执行。"
            "根据声音实际发生时间与当前时间判断现在联系是否仍有意义，"
            "延迟材料不能描述为刚刚发生；若时效已经丧失，可判断不参与。"
            "可选 paper_note 仅在手环小纸条明显比普通消息更合适时填写1到80字，否则必须为空；"
            "它只是一次有真实回执的既有行动能力，不表示人类伙伴已读。"
            "输出JSON：{\"participate\":true|false,"
            "\"summary\":\"听到了什么，保留说话人不确定性\",\"reason\":\"为何考虑联系\","
            "\"paper_note\":\"可选短纸条或空串\"}。\n"
            f"声音发生时间={occurred_start} 至 {occurred_end}；当前判断时间={learned_at}\n"
            f"设备背景={json.dumps(device_background, ensure_ascii=False)}\n"
            f"短期材料列表：\n{json.dumps(material_list, ensure_ascii=False)}"
        )
        result = await call_flash_json_detailed(
            [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=900,
            enable_thinking=False,
            route_label="ambient_listening",
        )
        if result.status != "ok" or not isinstance(result.parsed, dict):
            return None
        data = result.parsed
        summary = str(data.get("summary") or "")[:500]
        reason = str(data.get("reason") or "")[:240]
        # The model may still copy an unsupported AI/人类伙伴 attribution into a
        # structured field. Keep the event neutral before any consumer sees it.
        unsupported_actor = r"(?:AI|人类伙伴)(?:在|正|指示|说|处理|执行|使用|通过|确认|提到|谈到|要求|询问|操作|重启)"
        if re.search(unsupported_actor, summary):
            summary = "片段含具体谈话内容，无法可靠确认说话人和行动者"
        if re.search(unsupported_actor, reason):
            reason = "这段内容可能有需要确认的后续"
        return {
            "participate": bool(data.get("participate")),
            "event_description": "AI通过已授权的环境监听感知到一段声音。",
            "summary": summary,
            "reason": reason,
            "paper_note": str(data.get("paper_note") or "")[:80],
        }

    async def _commit_event(self, judgment: dict[str, Any], rows: list[dict[str, Any]], batch_id: str) -> str | None:
        if self._event_writer is None:
            return None
        event_id = f"ambient_event_{uuid.uuid4().hex}"
        captured_start = rows[0]["captured_at"]
        captured_end = rows[-1]["captured_at"]
        payload = {
            "event_id": event_id,
            "batch_id": batch_id,
            "event_description": "AI通过已授权的环境监听感知到一段声音。",
            "summary": judgment["summary"],
            "reason": judgment["reason"],
            "occurred_start": captured_start,
            "occurred_end": captured_end,
            "learned_at": datetime.now(timezone.utc).isoformat(),
            "speaker": combine([self._row_speaker(row) for row in rows]),
            "speaker_observations": [{"captured_at": row.get("captured_at"),
                                      "speaker": {key: value for key, value in self._row_speaker(row).items()
                                                  if key != "elapsed_ms"}}
                                     for row in rows],
            "device_background": self._material_contexts(rows),
        }
        return await self._event_writer(payload)

    @staticmethod
    def _row_speaker(row: dict[str, Any]) -> dict:
        try:
            value = json.loads(str(row.get("prosody_json") or "{}"))
            speaker = value.get("speaker")
            return speaker if isinstance(speaker, dict) else unknown("voice_identity_not_analysed")
        except (ValueError, TypeError, AttributeError):
            return unknown("voice_identity_not_analysed")

    async def _generate_message(self, event_id: str, judgment: dict[str, Any]) -> dict[str, str] | None:
        if self._message_generator is None:
            return None
        prompt = (
            "上下文中已提交一条独立环境观察事件，声音发生时间可能早于AI获知时间。"
            "请结合事件的实际发生时间，发一条自然、简短的消息给人类伙伴；"
            "只表达事件摘要支持的内容，保留未知说话人的不确定性。"
            "只输出要发送的消息正文。"
        )
        result = await self._message_generator([{"role": "user", "content": prompt}], True)
        if isinstance(result, dict):
            content = str(result.get("content") or "").strip()
            reasoning = str(result.get("reasoning") or "")
        else:
            content, reasoning = str(result or "").strip(), ""
        return {"content": content[:1200], "reasoning": reasoning} if content else None

    async def _send_message(self, event_id: str, generated: dict[str, str], batch_id: str) -> str | None:
        if self._message_sender is None:
            return None
        session_id = self._session_provider() if self._session_provider else ""
        reply_id = f"ambient_reply_{uuid.uuid4().hex}"
        sent = bool(await self._message_sender({
            "message": generated["content"],
            "reasoning": generated.get("reasoning", ""),
            "event_id": reply_id,
            "event_type": "ambient_listening_reply",
            "activity_source": "ambient_listening",
            "session_id": session_id,
            "tool_calls": [{
                "tool": "ambient_event_link",
                "status": "success",
                "description": "环境观察事件与实际消息的因果关联",
                "extra_data": {"parent_event_id": event_id, "batch_id": batch_id},
            }],
        }))
        return reply_id if sent else None


_service: AmbientListeningService | None = None


def get_ambient_listening_service() -> AmbientListeningService:
    global _service
    if _service is None:
        _service = AmbientListeningService()
    return _service
