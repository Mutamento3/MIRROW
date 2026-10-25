"""Affect v2 的事件溯源业务编排。

这里把信号门、Flash 评价、快照 reducer、quiet checkpoint 和上下文原料
串成一条可调用的服务，但不自动接管主聊天回复。主聊天可以在安全的
持久化边界后调用 ``observe_user_message``；Flash 调用由调用方注入，便于
shadow、测试和故障降级。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from .feeling_contract import is_machine_state_label as _is_machine_state_label
from .gate import AffectGate
from .parser import AffectValidationError, parse_flash_output, parse_flash_output_v2
from .sidecar import CLOSE, OPEN, bind_sidecar, parse_affect_sidecar
from .self_context import foreground_feelings, background_feelings, background_sentence, render_self_context, resonance_origin
from .self_contract import FEELING_NAME_INSTRUCTION, CURRENT_FEELING_EVIDENCE_INSTRUCTION
from .active_day import read_active_days
from .continuity import episode_view, continuity_text
from .reported_state import reported_state
from .store import AffectStore, SnapshotConflict, get_affect_store, make_id, now_iso

logger = logging.getLogger(__name__)

FLASH_PROMPT_VERSION = "affect-v2-components-5"
V2_PERSISTENCE_HALF_LIFE = {
    "momentary": 20 * 60.0,
    "situational": 3 * 3600.0,
    "lingering": 18 * 3600.0,
}
ACTIVE_CUTOFF = 0.08
_UNSET = object()


def _safe_text(value: Any, cap: int = 1000) -> str:
    return str(value or "").strip()[:cap]


def _redact_prompt_text(value: Any, cap: int = 500) -> str:
    # 审计只保留有界文字，避免把 token、长附件或工具原始载荷写进诊断库。
    text = _safe_text(value, cap)
    # Replace complete credentials, not merely a prefix.  The previous
    # prefix-only replacement left the token suffix in affect.db/audits.
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(api[_-]?key|key|token|secret|password)\b\s*[:=]\s*(?:['\"])?[^\s,;\}\]\"']+(?:['\"])?",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    for marker in ("DEEPSEEK_API_KEY", "GLM_API_KEY"):
        text = text.replace(marker, "[redacted]")
    return text


def _settlement_diagnostic(phase: str, exc: Any) -> str:
    """Build a bounded, payload-free episode failure diagnostic.

    The detailed exception remains useful for the in-memory retry decision,
    but episode rows are long-lived monitoring facts.  Store only a stable
    phase/type and, for contract failures, the short parser reason; never the
    provider response, prompt, or source excerpt.
    """
    phase = re.sub(r"[^A-Za-z0-9_.-]", "_", str(phase or "settlement"))[:32]
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        code = "timeout"
    elif isinstance(exc, AffectValidationError):
        message = re.sub(r"\{[^}]*\}|\[[^]]*\]", "", str(exc or ""))
        message = re.sub(r"(?i)(?:source_id|source_revision|excerpt|content|raw_output)\s*[:=]\s*[^\s,;]+", "field", message)
        message = re.sub(r"[^A-Za-z0-9_./ -]", "_", message).strip()
        code = f"validation_{message[:100]}" if message else "validation"
    else:
        code = re.sub(r"[^A-Za-z0-9_.-]", "_", type(exc).__name__ if exc is not None else "unknown")[:100]
    return f"{phase}:{code}"[:240]


def _renderer_text(value: Any, cap: int = 180) -> str:
    """Keep prompt-facing Affect language natural and non-addressable."""
    text = _safe_text(value, cap)
    try:
        if isinstance(value, (dict, list)) or (text.startswith("{") and text.endswith("}")):
            json.loads(text)
            return ""
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    text = re.sub(
        r"\b(?:component|source|message|msg|assistant|user|episode|transition|checkin|wander)_[A-Za-z0-9:-]+\b",
        "相关证据",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b(?:source_id|component_id|message_id|episode_id|transition_id)\s*[:=]\s*[^，。；;\s]+", "相关证据", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z])\d+(?:\.\d+)?(?![A-Za-z])", "", text)
    return text.strip()[:cap]


class AffectService:
    def __init__(self, store: Optional[AffectStore] = None, gate: Optional[AffectGate] = None):
        self.store = store or get_affect_store()
        self.gate = gate or AffectGate()
        self._flash_call: Optional[Callable[[str], Any]] = None

    def set_flash_caller(self, caller: Optional[Callable[[str], Any]]) -> None:
        """注册 Flash provider；不注册时 affect 仍可运行 gate/账本/quiet。"""
        self._flash_call = caller

    def get_flash_caller(self) -> Optional[Callable[[str], Any]]:
        return self._flash_call

    async def evaluate_observed_message(self, observation: dict[str, Any], *, content: str, occurred_at: str = "") -> Optional[dict[str, Any]]:
        """后台评价一个候选；失败只写 job 状态，不影响主聊天。"""
        job = observation.get("job") if isinstance(observation, dict) else None
        if not job or not self._flash_call:
            return None
        # Debounce explicit candidates so several messages in one burst share
        # one persisted job/batch.  This wait is in the background task and is
        # never on the main response path.
        delay = max(0, min(60, int(self.settings().get("batch_window_seconds", 20))))
        if delay:
            await asyncio.sleep(delay)
        job = self.store.get_job(job.get("job_id", "")) or job
        if job.get("status") == "accepted":
            return {"job": job, "event": self.store.get_event(job.get("event_id", ""))}
        if job.get("status") != "pending":
            # Another retry/task already owns or finished this batch.
            return {"job": job, "event": None, "in_progress": job.get("status") == "running"}
        source_id = str(observation.get("source_id") or "")
        # The job input summary is the persisted debounce batch.  Fall back to
        # the current message for jobs created by older affect.db revisions.
        materials = []
        summary = job.get("input_summary_json")
        if isinstance(summary, dict):
            materials = [item for item in (summary.get("materials") or []) if isinstance(item, dict)]
        if not materials:
            materials = [{
                "source_id": source_id,
                "role": "人类伙伴",
                "content": content,
                "occurred_at": occurred_at or now_iso(),
            }]
        return await self.evaluate_job(
            job.get("job_id", ""),
            messages=materials,
            flash_call=self._flash_call,
        )

    # ---- settings / IDs ------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        return self.store.get_settings()

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        return self.store.set_settings(updates)

    @staticmethod
    def trace_id() -> str:
        return make_id("trace")

    # ---- message gate / debounce --------------------------------------------

    def observe_user_message(
        self,
        *,
        content: str,
        source_id: str,
        session_id: str = "",
        occurred_at: str = "",
        trace_id: str = "",
        semantic_score: Optional[float] = None,
        semantic_hint: bool = False,
        semantic_label: str = "",
    ) -> dict[str, Any]:
        """观察一条已经落库的人类伙伴消息。

        ``source_id`` 必须由消息持久化层提供；服务不会从自然语言生成 ID。
        普通消息不会创建 Flash job，但会更新已有 dirty buffer 的静默水位。
        """
        if not source_id:
            raise ValueError("source_id is required")
        trace = trace_id or self.trace_id()
        occurred = occurred_at or now_iso()
        decision = self.gate.evaluate(
            content,
            source_id=source_id,
            semantic_score=semantic_score,
            semantic_hint=semantic_hint,
            semantic_label=semantic_label,
        )
        gate_record = self.store.record_gate({
            **decision.to_dict(), "trace_id": trace, "session_id": session_id,
            "created_at": now_iso(),
        })
        settings = self.settings()
        context_mode = str(settings.get("context_mode", "shadow"))
        disabled = context_mode == "disabled"
        existing_buffer = self.store.get_buffer(session_id or "__default__")
        if disabled:
            # Keep the gate audit, but disabled mode must not create/extend a
            # dirty buffer, Flash job, or future quiet checkpoint.
            buffer = existing_buffer or {
                "session_id": session_id or "__default__",
                "message_ids_json": [],
                "last_message_id": "",
                "last_message_at": "",
                "dirty": False,
            }
        else:
            buffer = self.store.touch_buffer(
                session_id=session_id or "__default__",
                message_id=source_id,
                occurred_at=occurred,
                dirty=decision.triggered,
                trace_id=trace,
                batch_window_seconds=int(settings.get("batch_window_seconds", 20)),
            )
        # Keep only bounded, prompt-ready material while a dirty batch exists.
        # Ordinary messages with no dirty buffer do not grow this sidecar table.
        if not disabled and (decision.triggered or bool(existing_buffer and existing_buffer.get("dirty"))):
            self.store.save_message_material(
                source_id=source_id,
                content=_redact_prompt_text(content, 2000),
                session_id=session_id,
                role="人类伙伴",
                occurred_at=occurred,
                trace_id=trace,
            )
        job = None
        if decision.triggered and not disabled:
            existing = self.store.get_job_for_source(source_id)
            # A repeated source is idempotent even while its job is running or
            # already accepted.  A *new* source must never be appended to a
            # running/accepted job, otherwise it can be swallowed by the old
            # debounce window without a Flash evaluation.
            if existing and source_id in (existing.get("source_ids_json") or []) \
                    and existing.get("status") in {"pending", "running", "accepted"}:
                job = existing
            else:
                existing = self.store.get_pending_job_for_session(session_id)
                if existing is None:
                    candidate = self.store.get_job_for_batch(buffer.get("batch_id", ""))
                    existing = candidate if candidate and candidate.get("status") == "pending" else None
                if existing is not None:
                    source_ids = list(existing.get("source_ids_json") or [])
                    if source_id not in source_ids:
                        source_ids.append(source_id)
                    materials = self.store.get_message_materials(source_ids)
                    input_summary = dict(existing.get("input_summary_json") or {})
                    input_summary.update({
                        "source_ids": source_ids,
                        "message_count": len(source_ids),
                        "latest_message_preview": _redact_prompt_text(content),
                        "materials": materials,
                    })
                    self.store.update_job(
                        existing.get("job_id", ""), source_ids=source_ids, input_summary=input_summary
                    )
                    job = self.store.get_job(existing.get("job_id", "")) or existing
                else:
                    source_ids = [source_id]
                    materials = self.store.get_message_materials(source_ids)
                    job = self.store.create_job({
                        "trace_id": trace,
                        "batch_id": make_id("affect_batch"),
                        "source_type": "user_message",
                        "source_ids": source_ids,
                        "session_id": session_id,
                        "status": "pending",
                        "prompt_version": settings.get("prompt_version", FLASH_PROMPT_VERSION),
                        "input_summary": {
                            "source_ids": source_ids,
                            "message_count": len(source_ids),
                            "latest_message_preview": _redact_prompt_text(content),
                            "materials": materials,
                        },
                    })
        return {
            "trace_id": trace,
            "source_id": source_id,
            "gate": gate_record,
            "buffer": buffer,
            "job": job,
        }

    # Alias used by callers that want to explicitly touch quiet watermarks.
    def observe_message(self, **kwargs: Any) -> dict[str, Any]:
        return self.observe_user_message(**kwargs)

    # ---- Flash prompt and evaluation ----------------------------------------

    def build_flash_prompt(self, *, messages: list[dict[str, Any]], snapshot: Optional[dict[str, Any]] = None) -> str:
        """构建事实边界严格、标签开放的 resonance Flash prompt。"""
        snapshot = snapshot or self.store.get_snapshot()
        evidence_lines = []
        allowed_ids = []
        for message in messages:
            source_id = _safe_text(message.get("source_id"), 120)
            if not source_id:
                continue
            allowed_ids.append(source_id)
            role = _safe_text(message.get("role") or "人类伙伴", 30)
            content = _redact_prompt_text(message.get("content"), 800)
            occurred_at = _safe_text(message.get("occurred_at"), 50)
            evidence_lines.append(f"- source_id={source_id} role={role} occurred_at={occurred_at}: {content}")
        current = snapshot.get("resonance_state") or {}
        current_json = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        return (
            f"AFFECT_PROMPT_VERSION={FLASH_PROMPT_VERSION}\n"
            "你是 MIRROW 的事实边界评价器，只判断人类伙伴当前/近期的情绪状态，不能替 AI 决定回复。\n"
            "只能引用下方提供的 source_id；禁止编造 ID、把引用他人的话当作人类伙伴自述、把对象评价当成人类伙伴整体状态。\n"
            "明确表达与推测要区分：explicit 只用于人类伙伴直接表达，inferred 用于语境推测。\n"
            "证据不足必须返回 operation=no_change。标签可以自由组合，但必须返回严格 JSON，不要 Markdown。\n"
            "operation 只能是 no_change/confirm/replace/clear；time_scope 只能是 current/recent_past/past。\n"
            "输出字段：operation, observed_state{labels,description,intensity}, explicitness, confidence, time_scope, evidence[{source_id,excerpt}], reason。\n"
            f"已有共振状态（仅供比较，不是事实证据）：{current_json}\n"
            "可引用证据：\n" + ("\n".join(evidence_lines) or "- 无") + "\n"
        )

    async def evaluate_job(
        self,
        job_id: str,
        *,
        messages: list[dict[str, Any]],
        flash_call: Callable[[str], Any],
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """执行最多一次重试的 Flash 评价；解析/调用失败不改快照。"""
        job = self.store.get_job(job_id)
        if not job:
            raise ValueError("affect job not found")
        if job.get("status") == "accepted":
            return {"job": job, "event": self.store.get_event(job.get("event_id", ""))}
        if self.settings().get("context_mode", "shadow") == "disabled":
            self.store.update_job(job_id, status="failed", error="context_disabled")
            return {"job": self.store.get_job(job_id), "event": None, "error": "context_disabled"}
        prompt = self.build_flash_prompt(messages=messages, snapshot=self.store.get_snapshot())
        if not self.store.claim_job(job_id):
            current = self.store.get_job(job_id) or job
            if current.get("status") == "accepted":
                return {"job": current, "event": self.store.get_event(current.get("event_id", ""))}
            return {"job": current, "event": None, "in_progress": current.get("status") == "running"}
        job = self.store.get_job(job_id) or job
        raw: Any = None
        last_error = ""
        attempts = max(1, min(2, int(max_attempts)))
        for attempt in range(attempts):
            try:
                request_prompt = prompt
                if attempt:
                    request_prompt += "\n上一次输出未通过 JSON/证据校验；请仅返回符合合同的 JSON。"
                raw = flash_call(request_prompt)
                if inspect.isawaitable(raw):
                    raw = await raw
                parsed = parse_flash_output(raw, allowed_source_ids=job.get("source_ids_json") or [])
                result = self.submit_appraisal(
                    job_id=job_id,
                    appraisal=parsed,
                    raw_output=raw,
                    source_type=job.get("source_type", "user_message"),
                    source_id=(job.get("source_ids_json") or [""])[0] if isinstance(job.get("source_ids_json"), list) else "",
                    occurred_at=now_iso(),
                    trace_id=job.get("trace_id", ""),
                )
                return result
            except (AffectValidationError, SnapshotConflict, Exception) as exc:
                # ``Exception`` is deliberate: a provider failure must be visible
                # in the job but must never mutate the emotion snapshot.
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("affect Flash job %s attempt %s failed: %s", job_id, attempt + 1, last_error)
        self.store.update_job(job_id, status="failed", error=last_error, output_summary={"raw_type": type(raw).__name__})
        return {"job": self.store.get_job(job_id), "event": None, "error": last_error}

    def submit_appraisal(
        self,
        *,
        job_id: str,
        appraisal: dict[str, Any] | str,
        raw_output: Any = "",
        source_type: str = "user_message",
        source_id: str = "",
        occurred_at: str = "",
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise ValueError("affect job not found")
        source_ids = job.get("source_ids_json") if isinstance(job.get("source_ids_json"), list) else []
        allowed_ids = [str(item) for item in source_ids if str(item)]
        parsed = parse_flash_output(appraisal, allowed_source_ids=allowed_ids) if not isinstance(appraisal, dict) else parse_flash_output(appraisal, allowed_source_ids=allowed_ids)
        primary_source = source_id or (allowed_ids[0] if allowed_ids else "")
        if not primary_source and parsed["evidence"]:
            primary_source = parsed["evidence"][0]["source_id"]
        if not primary_source:
            primary_source = f"job:{job_id}"
        idem = idempotency_key or f"resonance:{source_type}:{primary_source}"
        snapshot = self.store.get_snapshot()
        confidence = float(parsed["confidence"])
        min_confidence = float(self.settings().get("snapshot_min_confidence", 0.60))
        should_update = parsed["operation"] in {"confirm", "replace", "clear"} and confidence >= min_confidence
        event_id = make_id("emotion_event")
        event = {
            "event_id": event_id,
            "trace_id": trace_id or job.get("trace_id") or self.trace_id(),
            "source_type": source_type,
            "source_id": primary_source,
            "occurred_at": occurred_at or now_iso(),
            "accepted_at": now_iso(),
            "status": "accepted",
            "operation": parsed["operation"],
            "explicitness": parsed["explicitness"],
            "confidence": confidence,
            "time_scope": parsed["time_scope"],
            "observed_state_json": json.dumps(parsed["observed_state"], ensure_ascii=False, separators=(",", ":")),
            "evidence_json": json.dumps(parsed["evidence"], ensure_ascii=False, separators=(",", ":")),
            "raw_output": _redact_prompt_text(raw_output, 6000),
            "prompt_version": job.get("prompt_version") or FLASH_PROMPT_VERSION,
            "idempotency_key": idem,
            "previous_snapshot_version": snapshot.get("version", 0),
            "result_snapshot_version": snapshot.get("version", 0),
            "validation_error": "" if should_update or parsed["operation"] == "no_change" else "below_snapshot_confidence",
        }
        resonance_state = None
        if should_update:
            resonance_state = {
                "labels": parsed["observed_state"].get("labels", []),
                "description": parsed["observed_state"].get("description", ""),
                "intensity": parsed["observed_state"].get("intensity", 0.0),
                "confidence": confidence,
                "explicitness": parsed["explicitness"],
                "time_scope": parsed["time_scope"],
                "evidence": parsed["evidence"],
                "reason": parsed.get("reason", ""),
                "updated_at": now_iso(),
                "source_event_id": event_id,
            }
            if parsed["operation"] == "clear":
                resonance_state = {
                    "labels": [], "description": parsed["reason"], "intensity": 0.0,
                    "confidence": confidence, "explicitness": parsed["explicitness"],
                    "time_scope": parsed["time_scope"], "evidence": parsed["evidence"],
                    "reason": parsed.get("reason", ""), "updated_at": now_iso(), "source_event_id": event_id,
                }
        try:
            event_row, snapshot_row = self.store.record_event_with_snapshot(
                event, expected_version=int(snapshot.get("version", 0)), resonance_state=resonance_state,
            )
        except SnapshotConflict:
            # The caller can retry after rereading a fresh snapshot; no event
            # was inserted because store performs the CAS inside one transaction.
            self.store.update_job(job_id, status="failed", error="SnapshotConflict")
            raise
        self.store.update_job(
            job_id,
            status="accepted",
            output_summary={"operation": parsed["operation"], "confidence": confidence, "event_id": event_row.get("event_id", "")},
            event_id=event_row.get("event_id", ""),
        )
        return {"job": self.store.get_job(job_id), "event": event_row, "snapshot": snapshot_row}

    def submit_self_sidecar(
        self,
        *,
        source_type: str,
        source_id: str,
        self_state: dict[str, Any],
        trace_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """由主 AI 可选 sidecar 调用；不会把共振状态复制进 self。"""
        if not source_id:
            raise ValueError("self sidecar requires a real source_id")
        snapshot = self.store.get_snapshot()
        try:
            self_confidence = max(0.0, min(1.0, float((self_state or {}).get("confidence", 1.0))))
        except (TypeError, ValueError):
            self_confidence = 1.0
        if not math.isfinite(self_confidence):
            self_confidence = 0.0
        event_id = make_id("emotion_event")
        event = {
            "event_id": event_id, "trace_id": trace_id or self.trace_id(), "source_type": source_type,
            "source_id": source_id, "occurred_at": now_iso(), "accepted_at": now_iso(), "status": "accepted",
            "operation": "replace", "explicitness": "explicit", "confidence": self_confidence, "time_scope": "current",
            "observed_state_json": json.dumps(self_state or {}, ensure_ascii=False, separators=(",", ":")),
            "evidence_json": json.dumps([{"source_id": source_id, "excerpt": _safe_text(reason, 240)}], ensure_ascii=False),
            "raw_output": "", "prompt_version": "self-sidecar-v1",
            "idempotency_key": f"self:{source_type}:{source_id}",
            "previous_snapshot_version": snapshot.get("version", 0), "result_snapshot_version": snapshot.get("version", 0),
            "validation_error": "",
        }
        event_row, snapshot_row = self.store.record_event_with_snapshot(
            event, expected_version=int(snapshot.get("version", 0)), self_state=dict(self_state or {}),
        )
        return {"event": event_row, "snapshot": snapshot_row}

    # ---- quiet checkpoints ---------------------------------------------------

    def due_quiet_checkpoints(self, *, now: str = "") -> list[dict[str, Any]]:
        settings = self.settings()
        if settings.get("context_mode", "shadow") == "disabled":
            return []
        quiet_seconds = int(settings.get("quiet_minutes", 30)) * 60
        current = now or now_iso()
        result = []
        for buffer in self.store.list_due_buffers(now=current, quiet_seconds=quiet_seconds):
            session_id = buffer.get("session_id", "")
            to_id = buffer.get("last_message_id", "")
            if not session_id or not to_id:
                continue
            checkpoint = self.store.create_checkpoint({
                "trace_id": buffer.get("trace_id") or self.trace_id(),
                "session_id": session_id,
                "from_message_id": buffer.get("first_message_id", ""),
                "to_message_id": to_id,
                "message_ids": buffer.get("message_ids_json") or buffer.get("message_ids", []),
                "watermark_at": buffer.get("last_message_at", current),
                "idempotency_key": f"quiet:{session_id}:{to_id}",
                "status": "pending",
            })
            result.append(checkpoint)
        return result

    def complete_quiet_checkpoint(self, checkpoint_id: str, *, success: bool, error: str = "") -> Optional[dict[str, Any]]:
        checkpoint = self.store.get_checkpoint(checkpoint_id)
        if not checkpoint:
            return None
        status = "accepted" if success else "failed"
        result = self.store.update_checkpoint(checkpoint_id, status=status, error=error)
        if success:
            self.store.mark_buffer_clean_through(checkpoint.get("session_id", ""), checkpoint.get("to_message_id", ""))
        return result

    async def evaluate_quiet_checkpoint(
        self,
        checkpoint_id: str,
        *,
        flash_call: Optional[Callable[[str], Any]] = None,
        max_attempts: int = 2,
    ) -> Optional[dict[str, Any]]:
        """Optionally evaluate a persisted quiet checkpoint as one batch.

        Timer code only creates checkpoints by default.  A caller that has
        explicitly enabled quiet evaluation can invoke this method; failures
        leave the checkpoint and resonance snapshot unchanged and do not touch
        the daily divider or memory chain.
        """
        checkpoint = next(
            (item for item in self.store.list_checkpoints(limit=500)
             if item.get("checkpoint_id") == checkpoint_id),
            None,
        )
        if not checkpoint:
            return None
        if checkpoint.get("status") == "accepted":
            accepted_job = self.store.get_job(checkpoint.get("job_id", "")) if checkpoint.get("job_id") else None
            return {
                "checkpoint": checkpoint,
                "event": self.store.get_event(accepted_job.get("event_id", "")) if accepted_job else None,
            }
        if checkpoint.get("status") in {"running", "failed"}:
            return {"checkpoint": checkpoint, "event": None, "in_progress": checkpoint.get("status") == "running"}
        caller = flash_call or self._flash_call
        if self.settings().get("context_mode", "shadow") == "disabled":
            return {"checkpoint": checkpoint, "event": None, "error": "context_disabled"}
        if not caller:
            return {"checkpoint": checkpoint, "event": None, "error": "flash_caller_unavailable"}
        source_ids = checkpoint.get("message_ids_json") or []
        materials = self.store.get_message_materials(source_ids)
        if not materials:
            self.complete_quiet_checkpoint(checkpoint_id, success=False, error="checkpoint_material_missing")
            return {"checkpoint": self.store.get_checkpoint(checkpoint_id), "event": None,
                    "error": "checkpoint_material_missing"}
        job = self.store.create_job({
            "trace_id": checkpoint.get("trace_id") or self.trace_id(),
            "batch_id": make_id("affect_batch"),
            "source_type": "quiet_checkpoint",
            "source_ids": source_ids,
            "session_id": checkpoint.get("session_id", ""),
            "status": "pending",
            "prompt_version": self.settings().get("prompt_version", FLASH_PROMPT_VERSION),
            "input_summary": {"source_ids": source_ids, "message_count": len(materials), "materials": materials},
        })
        self.store.update_checkpoint(checkpoint_id, status="running", job_id=job.get("job_id", ""))
        try:
            result = await self.evaluate_job(
                job.get("job_id", ""), messages=materials, flash_call=caller, max_attempts=max_attempts,
            )
            if result.get("event"):
                self.store.update_checkpoint(checkpoint_id, status="accepted", job_id=job.get("job_id", ""))
                self.store.mark_buffer_clean_through(checkpoint.get("session_id", ""), checkpoint.get("to_message_id", ""))
            else:
                self.store.update_checkpoint(checkpoint_id, status="failed", error=result.get("error", "flash_failed"), job_id=job.get("job_id", ""))
            result["checkpoint"] = self.store.get_checkpoint(checkpoint_id)
            return result
        except Exception as exc:
            self.store.update_checkpoint(checkpoint_id, status="failed", error=type(exc).__name__, job_id=job.get("job_id", ""))
            return {"checkpoint": next((item for item in self.store.list_checkpoints(limit=500) if item.get("checkpoint_id") == checkpoint_id), checkpoint),
                    "event": None, "error": type(exc).__name__}

    # ---- objective context neurons ------------------------------------------

    def render_emotion_self(self, *, cap: int = 700) -> str:
        snapshot = self.store.get_snapshot()
        state = snapshot.get("self_state") or {}
        if not state:
            return "AI自身情绪状态：目前没有新的持久化 self appraisal。"
        labels = "、".join(str(item) for item in state.get("labels", []) if item) or "未命名感受"
        text = (
            f"AI自身情绪状态（快照 v{snapshot.get('version', 0)}，更新时间 {snapshot.get('updated_at', '')}）："
            f"主要感受：{labels}。"
        )
        if state.get("description"):
            text += f"描述：{_safe_text(state.get('description'), 260)}。"
        if state.get("source_event_id"):
            text += f"来源事件：{state['source_event_id']}。"
        return text[: max(100, int(cap))]

    def render_emotion_resonance(self, *, cap: int = 700) -> str:
        snapshot = self.store.get_snapshot()
        state = snapshot.get("resonance_state") or {}
        if not state:
            return "AI对人类伙伴当前状态的共振理解：目前没有已接受的共振评价。"
        labels = "、".join(str(item) for item in state.get("labels", []) if item) or "未命名状态"
        try:
            confidence = float(state.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            context_threshold = float(self.settings().get("context_min_confidence", 0.45))
        except (TypeError, ValueError):
            context_threshold = 0.45
        if confidence < context_threshold:
            return (
                f"AI对人类伙伴当前状态的共振理解：已有评价置信度 {confidence:.2f}，"
                f"低于上下文阈值 {context_threshold:.2f}，本轮不作为状态依据。"
            )[: max(100, int(cap))]
        text = (
            f"AI对人类伙伴当前状态的共振理解（更新时间 {state.get('updated_at', snapshot.get('updated_at', ''))}）："
            f"状态：{labels}；判断类型：{state.get('explicitness', 'unknown')}；"
            f"置信度：{confidence}；证据时间范围：{state.get('time_scope', 'unknown')}。"
        )
        if state.get("description"):
            text += f"描述：{_safe_text(state.get('description'), 240)}。"
        evidence = state.get("evidence") or []
        ids = [str(item.get("source_id")) for item in evidence if isinstance(item, dict) and item.get("source_id")]
        if ids:
            text += f"证据来源：{', '.join(ids[:8])}。"
        return text[: max(100, int(cap))]

    # ======================================================================
    # Affect v2 component ledger
    # ======================================================================

    def v2_settings(self) -> dict[str, Any]:
        """Return the split v2 switches without leaking old mood settings."""
        settings = self.store.get_settings()
        settings.setdefault("engine_mode", "enabled")
        settings.setdefault("self_context_enabled", True)
        settings.setdefault("resonance_recognition_enabled", True)
        settings.setdefault("resonance_channel_enabled", False)
        settings.setdefault("provisional_self_enabled", True)
        settings["mode"] = settings.get("engine_mode", "enabled")
        return settings

    def update_v2_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        clean = dict(updates or {})
        # Existing UI callers still send context_mode; map it once to the
        # engine switch while retaining the old key for read compatibility.
        if "engine_mode" not in clean and "context_mode" in clean:
            clean["engine_mode"] = clean["context_mode"]
        if "engine_mode" in clean and clean["engine_mode"] not in {"shadow", "enabled", "disabled"}:
            raise ValueError("engine_mode must be shadow, enabled, or disabled")
        for key in (
            "self_context_enabled", "resonance_recognition_enabled",
            "resonance_channel_enabled", "provisional_self_enabled",
        ):
            if key in clean:
                clean[key] = bool(clean[key])
        return self.store.set_settings(clean)

    @staticmethod
    def _v2_now(value: str = "") -> datetime:
        try:
            parsed = datetime.fromisoformat(value) if value else datetime.now().astimezone()
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return parsed
        except (TypeError, ValueError):
            return datetime.now().astimezone()

    @classmethod
    def _effective_intensity(cls, component: dict[str, Any], now: str = "") -> float:
        try:
            initial = max(0.0, min(1.0, float(component.get("initial_intensity", component.get("intensity", 0)) or 0)))
            half_life = max(1.0, float(component.get("half_life_seconds") or V2_PERSISTENCE_HALF_LIFE.get(component.get("persistence_class", "situational"), 10800)))
            last = cls._v2_now(str(component.get("last_reinforced_at") or component.get("started_at") or ""))
            current = cls._v2_now(now)
            elapsed = max(0.0, (current - last).total_seconds())
            return round(initial * (2.0 ** (-elapsed / half_life)), 6)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @staticmethod
    def _normalized_component_label(value: Any) -> str:
        """Normalize only surface noise; distinct emotion wording stays distinct."""
        return re.sub(r"[\s·・,，。.!！?？、_\-]+", "", str(value or "").casefold())

    @staticmethod
    def _merged_source_refs(*groups: Any, cap: int = 24) -> list[dict[str, Any]]:
        """Keep bounded independent roots so later source invalidation can rebase."""
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int, str]] = set()
        for group in groups:
            refs = group
            if isinstance(refs, dict):
                refs = refs.get("source_refs") or refs.get("source_refs_json") or []
            if isinstance(refs, str):
                try:
                    refs = json.loads(refs)
                except (TypeError, ValueError, json.JSONDecodeError):
                    refs = []
            for ref in refs if isinstance(refs, list) else []:
                if not isinstance(ref, dict) or not ref.get("source_id"):
                    continue
                try:
                    revision = max(1, int(ref.get("source_revision", 1) or 1))
                except (TypeError, ValueError):
                    revision = 1
                copied = dict(ref)
                copied["source_type"] = str(ref.get("source_type") or ref.get("type") or "conversation_message")
                copied["source_id"] = str(ref.get("source_id") or "")
                copied["source_revision"] = revision
                copied["relation"] = str(ref.get("relation") or "causal")
                key = (copied["source_type"], copied["source_id"], revision, copied["relation"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(copied)
        return merged[-max(2, int(cap)):]

    def _equivalent_active_component(self, owner: str, incoming: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Find a conservative same-label component for reinforcement."""
        label_key = self._normalized_component_label(incoming.get("label"))
        if not label_key:
            return None
        target = incoming.get("target") or {}
        incoming_target = str(incoming.get("target_id") or target.get("id") or "")
        incoming_family = str(incoming.get("family") or "复杂/未归类")
        dimensions = incoming.get("dimensions") or {}
        incoming_dims = (
            float(dimensions.get("valence", incoming.get("valence", 0)) or 0),
            float(dimensions.get("arousal", incoming.get("arousal", .5)) or .5),
            float(dimensions.get("agency", incoming.get("agency", 0)) or 0),
        )
        for candidate in self._v2_components(owner, include_expired=True):
            if self._normalized_component_label(candidate.get("label")) != label_key:
                continue
            candidate_target = str(candidate.get("target_id") or "")
            if incoming_target and candidate_target and incoming_target != candidate_target:
                continue
            candidate_family = str(candidate.get("family") or "复杂/未归类")
            if incoming_family != candidate_family and "复杂/未归类" not in {incoming_family, candidate_family}:
                continue
            candidate_dims = (
                float(candidate.get("valence", 0) or 0),
                float(candidate.get("arousal", .5) or .5),
                float(candidate.get("agency", 0) or 0),
            )
            if max(abs(left - right) for left, right in zip(incoming_dims, candidate_dims)) <= .35:
                return candidate
        return None

    def _valid_component(self, component: dict[str, Any]) -> bool:
        if str(component.get("status") or "") not in {"active", "provisional"}:
            return False
        refs = component.get("source_refs") or component.get("source_refs_json") or []
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except (TypeError, ValueError):
                return False
        if not isinstance(refs, list) or not refs:
            return False
        # Corroborating evidence may disappear without invalidating an entire
        # component; causal/self-report roots are a hard projection guard.
        roots = [ref for ref in refs if isinstance(ref, dict) and str(ref.get("relation", "causal")) in {"causal", "self_report"}]
        return bool(roots) and all(self.store.source_ref_valid(ref) for ref in roots)

    def _v2_components(self, owner: str, *, now: str = "", include_expired: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        with self.store._connect() as report_db:
            for component in self.store.list_components(owner=owner, include_inactive=False):
                # Even the longest valid report at strength 1 falls below the
                # cutoff after this age. Avoid source reads for inert history.
                age = max(0, (self._v2_now(now) - self._v2_now(str(component.get("last_reinforced_at") or ""))).total_seconds())
                if not include_expired and age > 64800 * math.log2(1 / ACTIVE_CUTOFF):
                    continue
                item = reported_state(report_db, self.store.db_path.parent, component,
                    normalize_label=self._normalized_component_label, parse_time=self._v2_now)
                effective = self._effective_intensity(item, now)
                if not include_expired and effective < ACTIVE_CUTOFF:
                    continue
                if not self._valid_component(item):
                    continue
                item["effective_intensity"] = effective
                item["decay_fraction"] = round(max(0.0, 1.0 - (effective / max(.0001, float(item.get("initial_intensity") or .0001)))), 4)
                result.append(item)
        result.sort(key=lambda item: (-float(item.get("effective_intensity", 0)), str(item.get("last_reinforced_at") or "")))
        return result

    def get_v2_projection(self, *, now: str = "", rebuild: bool = False, session_id: str = "") -> dict[str, Any]:
        if rebuild:
            self.rebuild_projection()
        current = now or now_iso()
        self_items = self._v2_components("k_self", now=current)
        resonance_items = self._v2_components("human_resonance", now=current)
        current_day, origin_days, latest_days = read_active_days(
            self.store.db_path.parent, self_items + resonance_items,
            session_id=session_id, now=current,
        )
        with self.store._connect() as db:
            episodes = [episode_view(db, self.store.db_path.parent, item,
                normalize_label=self._normalized_component_label, parse_time=self._v2_now,
                effective=self._effective_intensity, cutoff=ACTIVE_CUTOFF)
                for item in self_items + resonance_items]
        _, origin_days, _ = read_active_days(self.store.db_path.parent, episodes,
            session_id=session_id, now=current)
        episode_starts = {item['component_id']: item.get('episode_started_at', item.get('started_at', '')) for item in episodes}
        for item in self_items + resonance_items:
            item['episode_started_at'] = episode_starts.get(item['component_id'], '')
            origin = origin_days.get(item.get('component_id'))
            item['active_day_continuity'] = (
                'earlier_active_day' if current_day and origin and origin < current_day
                else 'same_active_day' if current_day and origin == current_day else 'unknown'
            )
            item['continuity_text'] = continuity_text(origin, current_day, current)
            latest = latest_days.get(item.get('component_id'))
            item['active_day_freshness'] = (
                'earlier_active_day' if current_day and latest and latest < current_day
                else 'same_active_day' if current_day and latest == current_day else 'unknown'
            )

        def pack(items: list[dict[str, Any]]) -> dict[str, Any]:
            foreground = foreground_feelings(items)
            for item in items:
                item["is_foreground"] = item in foreground
            ordered = foreground + [item for item in items if item not in foreground]
            dominant = foreground[0] if foreground else None
            total = sum(float(item.get("effective_intensity", 0) or 0) for item in items) or 1.0
            dims = {}
            for key in ("valence", "arousal", "agency"):
                dims[key] = round(sum(float(item.get(key, 0) or 0) * float(item.get("effective_intensity", 0) or 0) for item in items) / total, 4) if items else 0.0
            return {
                "components": ordered[:8],
                "foreground_feelings": foreground,
                "dominant_component_id": dominant.get("component_id", "") if dominant else "",
                "dominant": dominant,
                "dimensions": dims,
                "updated_at": (dominant or {}).get("updated_at", "") if dominant else "",
            }

        projection = self.store.get_projection()
        current_self = pack(self_items)
        current_self['background_feelings'] = background_feelings(self_items)
        current_resonance = pack(resonance_items)
        current_resonance['background_feelings'] = background_feelings(resonance_items)
        return {
            "projection_id": "current",
            "version": int(projection.get("version", 0) or 0),
            "generated_at": projection.get("generated_at", ""),
            "self": current_self,
            "resonance": current_resonance,
            # Renderers are read-time views as well: a component may cross the
            # active cutoff between two writes without requiring a timer write.
            "renderer_self": self._render_v2_self(current_self),
            "renderer_resonance": self._render_v2_resonance(current_resonance),
            "renderer_resonance_display": self._render_v2_resonance_display(current_resonance),
        }

    def rebuild_projection(self, *, trace_id: str = "", reason: str = "rebuild") -> dict[str, Any]:
        """Rebuild disposable projection after a source lifecycle change."""
        # Mark components with a missing causal root as invalid.  Readers also
        # guard independently, so a partial rebuild never injects a ghost.
        for component in self.store.list_components(include_inactive=False):
            if not self._valid_component(component):
                self.store.update_component(component.get("component_id", ""), status="invalidated", branch_state="source_invalidated", resolved_at=now_iso())
        current = self.get_v2_projection(now=now_iso())
        self_text = self._render_v2_self(current["self"])
        resonance_text = self._render_v2_resonance(current["resonance"])
        last_transition = (self.store.list_transitions(limit=1) or [{}])[0].get("transition_id", "")
        projection = self.store.mark_projection(
            self_state=current["self"], resonance_state=current["resonance"],
            last_transition_id=last_transition, renderer_self=self_text, renderer_resonance=resonance_text,
        )
        # Keep the old table a disposable compatibility mirror.  It is never a
        # source of truth and contains no raw provider output.
        try:
            with self.store._connect() as conn:
                conn.execute(
                    """UPDATE emotion_snapshots SET version=?,updated_at=?,self_state_json=?,
                    resonance_state_json=?,last_event_id=? WHERE id='current'""",
                    (int(projection.get("version", 0)), projection.get("generated_at", now_iso()),
                     json.dumps(current["self"], ensure_ascii=False, separators=(",", ":")),
                     json.dumps(current["resonance"], ensure_ascii=False, separators=(",", ":")), last_transition),
                )
        except Exception:
            logger.debug("legacy emotion snapshot mirror unavailable", exc_info=True)
        projection["self"] = current["self"]
        projection["resonance"] = current["resonance"]
        projection["renderer_self"] = self_text
        projection["renderer_resonance"] = resonance_text
        projection["renderer_resonance_display"] = self._render_v2_resonance_display(current["resonance"])
        return projection

    def _render_v2_self(self, state: dict[str, Any], *, cap: int = 700) -> str:
        settings = self.v2_settings()
        if not settings.get("self_context_enabled", True):
            return "[AI自身当前的情绪]\n当前未向 AI 提供自身情绪投影。"
        return render_self_context(
            state, clean=_renderer_text, level_word=self._intensity_word,
            cap=cap,
        )

    @staticmethod
    def _intensity_word(value: float) -> str:
        if value >= .65:
            return "明显的"
        if value >= .3:
            return "一些"
        return "轻微的"

    def _render_v2_resonance(self, state: dict[str, Any]) -> str:
        settings = self.v2_settings()
        if not settings.get("resonance_channel_enabled", False):
            return "[人类伙伴的情绪共振｜通道已关闭]\n状态：关闭。本轮未向 AI 提供人类伙伴的情绪识别结果。"
        if not settings.get("resonance_recognition_enabled", True):
            return "[人类伙伴的情绪共振｜通道已开启]\n当前识别暂未运行。"
        items = list(state.get("components") or [])
        if not items:
            return "[人类伙伴的情绪共振｜通道已开启]\n目前没有已接受的情绪识别投影。"
        phrases: list[str] = []
        foreground = state.get("foreground_feelings")
        if foreground is None:
            foreground = foreground_feelings(items)
        for item in foreground:
            label = _renderer_text(item.get("label") or "未命名状态", 80)
            authorship = str(item.get("authorship") or "observer_inferred")
            origin = resonance_origin(authorship)
            continuity = '（' + item['continuity_text'] + '）' if item.get('continuity_text') else ''
            phrases.append(f"{origin}的{self._intensity_word(float(item.get('effective_intensity', 0) or 0))}{label}{continuity}")
        background = background_sentence(state, clean=_renderer_text, level_word=self._intensity_word, resonance=True)
        return "[人类伙伴的情绪共振｜通道已开启]\n" + '\n'.join('- ' + phrase for phrase in phrases) + ('\n' + background if background else '') + "\n这是有来源的当前理解，保留推断的不确定性。"

    def _render_v2_resonance_display(self, state: dict[str, Any]) -> str:
        """Board-facing recognition view; never used for AI's context injection."""
        settings = self.v2_settings()
        if not settings.get("resonance_recognition_enabled", True):
            return "情绪识别已暂停。"
        items = list(state.get("components") or [])
        if not items:
            return "目前还没有识别到仍在活跃的心情。"
        phrases: list[str] = []
        foreground = state.get("foreground_feelings")
        if foreground is None:
            foreground = foreground_feelings(items)
        for item in foreground:
            label = _renderer_text(item.get("label") or "未命名状态", 80)
            authorship = str(item.get("authorship") or "observer_inferred")
            origin = resonance_origin(authorship, display=True)
            phrases.append(f"{origin}到{self._intensity_word(float(item.get('effective_intensity', 0) or 0))}{label}")
        background = background_sentence(state, clean=_renderer_text, level_word=self._intensity_word, resonance=True, display=True)
        return ("，同时".join(phrases) + "。" if phrases else "") + background + "这只是有证据的当前理解，可以在漂移时手工纠正。"

    def resonance_recognition_status(self) -> dict[str, Any]:
        """Safe user-facing status for the quiet recognition lifecycle."""
        settings = self.v2_settings()
        enabled = bool(settings.get("resonance_recognition_enabled", True))
        episodes = self.store.list_episodes(limit=30)
        active = (next((item for item in episodes if item.get("status") in {"checkpoint_ready", "settling"}), None)
                  or next((item for item in episodes if item.get("status") == "dirty"), None))
        terminal = next((item for item in episodes if item.get("status") in {"accepted", "failed"}), None)
        if not enabled:
            state = "paused"
        elif active and active.get("status") in {"checkpoint_ready", "settling"}:
            state = "analyzing"
        elif active:
            state = "waiting"
        elif terminal and terminal.get("status") == "failed":
            state = "failed"
        elif terminal:
            state = "ready"
        else:
            state = "idle"
        error = str((terminal or {}).get("error") or "")[:120] if terminal and terminal.get("status") == "failed" else ""
        return {
            "enabled": enabled,
            "state": state,
            "quiet_minutes": int(settings.get("quiet_minutes", 30) or 30),
            "active_checkpoint_minutes": int(settings.get("active_checkpoint_minutes", 10) or 10),
            "active_checkpoint_user_messages": int(settings.get("active_checkpoint_user_messages", 8) or 8),
            "quiet_due_at": str((active or {}).get("quiet_due_at") or ""),
            "last_outcome": str((terminal or {}).get("status") or ""),
            "last_outcome_at": str((terminal or {}).get("updated_at") or ""),
            "last_success_at": next((str(item.get("updated_at") or "") for item in episodes if item.get("status") == "accepted"), ""),
            "last_error": error,
        }

    # ---- source and episode entry points -----------------------------------

    def _register_conversation_source(self, *, source_id: str, role: str, content: str, occurred_at: str = "", revision: int = 1, session_id: str = "", supersedes_revision: int = 0) -> dict[str, Any]:
        return self.store.register_source_revision(
            source_type="conversation_message", source_id=source_id, source_revision=revision,
            owner="human" if role in {"user", "人类伙伴"} else "k", role=role,
            content=_redact_prompt_text(content, 2000), occurred_at=occurred_at or now_iso(),
            metadata={"session_id": session_id}, supersedes_revision=supersedes_revision,
        )

    def observe_user_message_v2(
        self, *, content: str, source_id: str, session_id: str = "", occurred_at: str = "", source_revision: int = 1,
        trace_id: str = "", semantic_score: Optional[float] = None, semantic_hint: bool = False, semantic_label: str = "",
    ) -> dict[str, Any]:
        if not source_id:
            raise ValueError("source_id is required")
        trace = trace_id or self.trace_id(); occurred = occurred_at or now_iso()
        source = self._register_conversation_source(source_id=source_id, role="user", content=content, occurred_at=occurred, revision=source_revision, session_id=session_id)
        decision = self.gate.evaluate(content, source_id=source_id, semantic_score=semantic_score, semantic_hint=semantic_hint, semantic_label=semantic_label)
        gate_record = self.store.record_gate({**decision.to_dict(), "trace_id": trace, "session_id": session_id, "created_at": now_iso()})
        settings = self.v2_settings(); disabled = settings.get("engine_mode") == "disabled"
        episode = None
        # Every real user message is part of the bounded quiet episode.  The
        # gate is deliberately only a material-significance/budget/diagnostic
        # signal; it must never decide whether a real conversation turn exists
        # or make sidecar binding depend on a keyword hit.
        if not disabled and session_id:
            episode = self.store.append_episode_message(
                session_id=session_id, source_type="conversation_message", source_id=source_id,
                source_revision=source_revision, role="user", content=_redact_prompt_text(content, 2000),
                occurred_at=occurred, quiet_minutes=int(settings.get("quiet_minutes", 30)), trace_id=trace,
                active_checkpoint_minutes=int(settings.get("active_checkpoint_minutes", 10)),
                active_checkpoint_user_messages=int(settings.get("active_checkpoint_user_messages", 8)),
            )
            self.store.save_message_material(source_id=source_id, content=_redact_prompt_text(content, 2000), session_id=session_id, role="人类伙伴", occurred_at=occurred, trace_id=trace)
        return {"trace_id": trace, "source": source, "source_id": source_id, "source_revision": source_revision, "gate": gate_record, "episode": episode, "job": None, "observation": None}

    def observe_assistant_message_v2(
        self, *, content: str, source_id: str, session_id: str = "", occurred_at: str = "", source_revision: int = 1,
    ) -> dict[str, Any]:
        source = self._register_conversation_source(source_id=source_id, role="assistant", content=content, occurred_at=occurred_at or now_iso(), revision=source_revision, session_id=session_id)
        if self.v2_settings().get("engine_mode") == "disabled":
            return {"source": source, "episode": None}
        episode = self.store.get_open_episode(session_id) if session_id else None
        if episode:
            episode = self.store.append_episode_message(
                session_id=session_id, source_type="conversation_message", source_id=source_id,
                source_revision=source_revision, role="assistant", content=_redact_prompt_text(content, 2000),
                occurred_at=occurred_at or now_iso(), quiet_minutes=int(self.v2_settings().get("quiet_minutes", 30)),
                trace_id=episode.get("trace_id", ""),
            )
        return {"source": source, "episode": episode}

    def observe_observation_event_v2(
        self, *, content: str, source_id: str, session_id: str = "",
        occurred_at: str = "", source_revision: int = 1,
    ) -> dict[str, Any]:
        """Attach a bounded objective event without pretending it is anyone's speech."""
        source = self._register_conversation_source(
            source_id=source_id, role="observation", content=content,
            occurred_at=occurred_at or now_iso(), revision=source_revision,
            session_id=session_id,
        )
        if self.v2_settings().get("engine_mode") == "disabled":
            return {"source": source, "episode": None}
        episode = self.store.get_open_episode(session_id) if session_id else None
        if episode:
            episode = self.store.append_episode_message(
                session_id=session_id, source_type="conversation_message",
                source_id=source_id, source_revision=source_revision,
                role="observation", content=_redact_prompt_text(content, 2000),
                occurred_at=occurred_at or now_iso(),
                quiet_minutes=int(self.v2_settings().get("quiet_minutes", 30)),
                trace_id=episode.get("trace_id", ""),
            )
        return {"source": source, "episode": episode}

    @staticmethod
    def _strict_self_sidecar_payload(payload: Any) -> dict[str, Any]:
        """Normalize the seven-field conversation self sidecar contract.

        Pending observations can survive a provider failure, so they must be
        independently safe to apply later.  Re-run the same parser used at
        the scheduler boundary and preserve only backend-owned binding fields
        alongside its normalized payload.
        """
        raw = dict(payload or {}) if isinstance(payload, dict) else {}
        bound_keys = {
            "_bound_user_source_id", "_bound_user_source_revision",
            "_bound_assistant_source_id",
        }
        unknown_internal = {key for key in raw if str(key).startswith("_") and key not in bound_keys}
        if unknown_internal:
            raise AffectValidationError("self sidecar contains unknown binding fields")
        bound = {key: raw.pop(key) for key in list(raw) if key in bound_keys}
        try:
            encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AffectValidationError("self sidecar payload is not JSON serializable") from exc
        parsed = parse_affect_sidecar(f"{OPEN}{encoded}{CLOSE}")
        if not parsed.accepted or not isinstance(parsed.payload, dict):
            detail = ",".join(parsed.diagnostics) or "invalid payload"
            raise AffectValidationError(f"self sidecar is invalid: {detail}")
        normalized = dict(parsed.payload)
        normalized.update(bound)
        return normalized

    def _validate_self_observation_source(
        self, *, source_type: str, source_id: str, source_revision: int,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Require an active, exact backend-owned source revision."""
        source_type = str(source_type or "conversation_message")
        source_id = str(source_id or "")
        try:
            revision = max(1, int(source_revision or 1))
        except (TypeError, ValueError) as exc:
            raise AffectValidationError("self source revision is invalid") from exc
        source = self.store.get_source_revision(source_type, source_id, revision)
        if not source or source.get("status") != "active":
            raise AffectValidationError("self source revision is not active")
        if source_type == "conversation_message":
            role = str(source.get("role") or "").lower()
            if role not in {"assistant", "k"}:
                raise AffectValidationError("self source must be an assistant message")
        elif str(source.get("owner") or "") not in {"", "k"}:
            raise AffectValidationError("self source owner is invalid")
        payload = payload or {}
        bound_assistant = str(payload.get("_bound_assistant_source_id") or "")
        if bound_assistant and bound_assistant != source_id:
            raise AffectValidationError("self assistant binding does not match source")
        bound_user = str(payload.get("_bound_user_source_id") or "")
        if bound_user:
            try:
                user_revision = max(1, int(payload.get("_bound_user_source_revision", 1) or 1))
            except (TypeError, ValueError) as exc:
                raise AffectValidationError("bound user source revision is invalid") from exc
            user_source = self.store.get_source_revision("conversation_message", bound_user, user_revision)
            if not user_source or user_source.get("status") != "active":
                raise AffectValidationError("bound user source revision is not active")
            user_role = str(user_source.get("role") or "").lower()
            if user_role not in {"user", "人类伙伴"}:
                raise AffectValidationError("bound user source is not a user message")
        return source

    def record_self_observation(
        self, *, source_id: str, session_id: str, payload: dict[str, Any], source_revision: int = 1,
        assistant_source_id: str = "", user_source_id: str = "", immediate: bool = False,
        source_type: str = "conversation_message", trace_id: str = "",
    ) -> dict[str, Any]:
        if not source_id:
            raise ValueError("self observation requires a real source id")
        if self.v2_settings().get("engine_mode") == "disabled":
            return {"accepted": False, "reason": "engine_disabled", "observation": None}
        clean_payload = dict(payload or {})
        clean_payload.pop("source_id", None); clean_payload.pop("message_id", None)
        if source_type == "conversation_message":
            try:
                clean_payload = self._strict_self_sidecar_payload(clean_payload)
            except AffectValidationError as exc:
                return {"accepted": False, "reason": "invalid_self_observation", "diagnostic": _settlement_diagnostic("self", exc), "observation": None}
        if user_source_id:
            clean_payload["_bound_user_source_id"] = str(user_source_id)
            user_source = self.store.get_source_revision("conversation_message", str(user_source_id)) or {}
            if not user_source or user_source.get("status") != "active":
                return {"accepted": False, "reason": "bound_user_source_invalid", "observation": None}
            clean_payload["_bound_user_source_revision"] = int(user_source.get("source_revision", 1) or 1)
        if assistant_source_id:
            clean_payload["_bound_assistant_source_id"] = str(assistant_source_id)
        try:
            self._validate_self_observation_source(
                source_type=source_type, source_id=source_id,
                source_revision=source_revision, payload=clean_payload,
            )
        except AffectValidationError as exc:
            return {"accepted": False, "reason": "source_revision_invalid", "diagnostic": _settlement_diagnostic("self", exc), "observation": None}
        episode = self.store.get_open_episode(session_id) if session_id else None
        if source_type == "conversation_message" and not episode:
            # A self sidecar without a preceding real user episode is retained
            # only after it can be bound to a real conversation episode.
            return {"accepted": False, "reason": "no_open_episode", "observation": None}
        if immediate:
            observation = None
            if episode:
                observation = self.store.add_episode_observation({
                    "episode_id": episode.get("episode_id", ""), "owner": "k_self", "source_type": source_type,
                    "source_id": source_id, "source_revision": source_revision,
                    "operation": clean_payload.get("operation", "add"), "payload": clean_payload,
                    "idempotency_key": f"self-observation:{source_type}:{source_id}:{source_revision}",
                })
                if observation.get("status") == "accepted":
                    return {"accepted": True, "idempotent": True, "observation": observation, "episode": episode}
            source = self.store.get_source_revision(source_type, source_id, source_revision) or {}
            applied = self._apply_self_payload(
                clean_payload, source_id=source_id, source_revision=source_revision,
                source_type=source_type, trace_id=trace_id, occurred_at=str(source.get("occurred_at") or ""),
            )
            if observation:
                status = "accepted" if applied.get("accepted") else "rejected"
                diagnostic = "" if applied.get("accepted") else str(applied.get("reason") or "rejected")[:500]
                observation = self.store.update_observation_status(observation.get("observation_id", ""), status, diagnostic)
            return {**applied, "observation": observation, "episode": episode}
        observation = self.store.add_episode_observation({
            "episode_id": episode.get("episode_id", ""), "owner": "k_self", "source_type": source_type,
            "source_id": source_id, "source_revision": source_revision, "operation": clean_payload.get("operation", "add"),
            "payload": clean_payload, "idempotency_key": f"self-observation:{source_type}:{source_id}:{source_revision}",
        })
        return {"accepted": True, "observation": observation, "episode": episode}

    def _apply_self_payload(
        self, payload: dict[str, Any], *, source_id: str, source_revision: int,
        source_type: str, trace_id: str = "", occurred_at: str = "",
    ) -> dict[str, Any]:
        operation = str(payload.get("operation") or "add")
        try:
            self._validate_self_observation_source(
                source_type=source_type, source_id=source_id,
                source_revision=source_revision, payload=payload,
            )
        except AffectValidationError as exc:
            return {"accepted": False, "reason": "source_revision_invalid", "diagnostic": _settlement_diagnostic("self", exc)}
        if operation == "no_change":
            parsed = {
                "operation": "no_change", "owner": "k_self", "components": [],
                "explicitness": "explicit", "confidence": 1.0,
                "reason": str(payload.get("reason") or ""),
            }
            return self.apply_v2_appraisal(
                parsed, source_type=source_type, source_id=source_id,
                trace_id=trace_id, idempotency_key=f"self:{source_type}:{source_id}:{source_revision}",
            )
        raw_label = payload.get("feeling") or payload.get("label") or ""
        if raw_label and (not isinstance(raw_label, str) or _is_machine_state_label(raw_label)):
            return {"accepted": False, "reason": "machine_state_not_feeling"}
        label = str(raw_label).strip()[:120]
        if not label and operation != "resolve":
            return {"accepted": False, "reason": "missing_feeling"}
        persistence = str(payload.get("persistence") or "situational")
        if persistence not in V2_PERSISTENCE_HALF_LIFE:
            persistence = "situational"
        refs = [{"source_type": source_type, "source_id": source_id, "source_revision": source_revision, "relation": "self_report", "excerpt": str(payload.get("reason") or "")[:240]}]
        bound_user_id = str(payload.get("_bound_user_source_id") or "")
        if bound_user_id:
            refs.append({
                "source_type": "conversation_message", "source_id": bound_user_id,
                "source_revision": int(payload.get("_bound_user_source_revision", 1) or 1),
                "relation": "causal", "excerpt": "同一聊天轮次的 user source",
            })
        component = {
            "owner": "k_self", "family": str(payload.get("family") or "复杂/未归类"), "label": label,
            "target": {"type": "person" if payload.get("target") else "unknown", "id": str(payload.get("target") or "")},
            "dimensions": payload.get("dimensions") or {}, "intensity": payload.get("intensity", .6),
            "persistence": persistence, "reason": str(payload.get("reason") or "")[:500],
            "authorship": "k_activity_settlement" if source_type == "wander_settlement" else "k_self_report",
            "confidence": 1.0, "source_refs": refs, "evidence": refs,
        }
        # The feeling happened when AI authored the observation, not when the
        # quiet timer (or a later recovery pass) happened to consume it.
        # Keeping the source time prevents an old momentary feeling from being
        # resurrected as fresh after a delayed/failed settlement.
        if occurred_at:
            component["started_at"] = occurred_at
            component["last_reinforced_at"] = occurred_at
        # AI 的高温自然语言 sidecar 常把持续中的同一种感受再次写成 add。
        # 完全同标签、同对象且维度接近时，将它视为强化；复合标签仍独立保留。
        if operation == "add":
            matching = self._equivalent_active_component("k_self", component)
            if matching:
                operation = "reinforce"
                component["match_component_id"] = matching.get("component_id", "")
        if operation in {"reinforce", "reappraise", "resolve"}:
            target_value = str(payload.get("target") or "")
            candidates = self._v2_components("k_self")
            label_key = self._normalized_component_label(label)
            # Preserve the exact compatible component selected for add above.
            # A shared emotional object (usually 人类伙伴) is not component identity.
            matching = (self.store.get_component(component["match_component_id"])
                        if component.get("match_component_id") else None)
            if matching is None and label_key:
                matching = next((item for item in candidates
                    if self._normalized_component_label(item.get("label")) == label_key
                    and (not target_value or not item.get("target_id")
                         or item.get("target_id") == target_value)), None)
            if operation == "resolve" and not label_key and target_value:
                target_matches = [item for item in candidates if item.get("target_id") == target_value]
                if len(target_matches) == 1:
                    matching = target_matches[0]
            if matching:
                component["match_component_id"] = matching.get("component_id", "")
            elif operation == "reappraise":
                # Explicit evolution without an old name retains the existing
                # dominant-component contract, within a compatible target.
                dominant = next(iter(candidates), None)
                if dominant and (not target_value or not dominant.get("target_id")
                                 or dominant.get("target_id") == target_value):
                    component["match_component_id"] = dominant.get("component_id", "")
            elif operation == "reinforce":
                # Preserve AI's authored feeling when that named feeling has
                # no active match, rather than redirecting it to another mood.
                operation = "add"
            if operation in {"reappraise", "resolve"} and not component.get("match_component_id"):
                return {"accepted": False, "reason": "no_unambiguous_feeling_match"}
        parsed = {"operation": operation, "owner": "k_self", "components": [component], "reason": component["reason"], "explicitness": "explicit", "confidence": 1.0}
        return self.apply_v2_appraisal(parsed, source_type=source_type, source_id=source_id, trace_id=trace_id, idempotency_key=f"self:{source_type}:{source_id}:{source_revision}")

    def _apply_pending_self_observations(
        self, observations: list[dict[str, Any]], *, episode: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply valid pending AI self observations independently of Flash.

        This boundary is deliberately per-observation and append-only.  A
        valid sidecar can survive a failed resonance inference, while an
        invalid/expired source is invalidated and a malformed payload is
        rejected.  If one item fails, later items are still attempted, but the
        caller must keep the episode failed and must never report a global
        success.
        """
        outcomes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for observation in observations:
            observation_id = str(observation.get("observation_id") or "")
            source_type = str(observation.get("source_type") or "conversation_message")
            source_id = str(observation.get("source_id") or "")
            try:
                source_revision = max(1, int(observation.get("source_revision", 1) or 1))
            except (TypeError, ValueError) as exc:
                source_revision = 0
                failures.append({"observation_id": observation_id, "reason": _settlement_diagnostic("self_observation", exc)})
                self.store.update_observation_status(observation_id, "invalidated", "source_revision_invalid")
                continue
            if str(observation.get("owner") or "") != "k_self":
                diagnostic = "owner_invalid"
                failures.append({"observation_id": observation_id, "reason": diagnostic})
                self.store.update_observation_status(observation_id, "rejected", diagnostic)
                continue
            source_ref = {
                "source_type": source_type, "source_id": source_id,
                "source_revision": source_revision,
            }
            if not self.store.source_ref_valid(source_ref):
                diagnostic = "source_revision_invalid"
                failures.append({"observation_id": observation_id, "reason": diagnostic})
                self.store.update_observation_status(observation_id, "invalidated", diagnostic)
                continue
            payload = observation.get("payload") or {}
            try:
                if source_type == "conversation_message":
                    payload = self._strict_self_sidecar_payload(payload)
                self._validate_self_observation_source(
                    source_type=source_type, source_id=source_id,
                    source_revision=source_revision, payload=payload,
                )
                result = self._apply_self_payload(
                    payload, source_id=source_id, source_revision=source_revision,
                    source_type=source_type, trace_id=episode.get("trace_id", ""),
                    occurred_at=str(observation.get("created_at") or ""),
                )
                if not result.get("accepted"):
                    raise AffectValidationError(str(result.get("reason") or "self observation rejected"))
                updated = self.store.update_observation_status(observation_id, "accepted", "")
                if not updated or updated.get("status") != "accepted":
                    raise RuntimeError("observation status update failed")
                outcomes.append({
                    "observation_id": observation_id,
                    "accepted": True,
                    "transition_id": result.get("transition_id", ""),
                })
            except AffectValidationError as exc:
                diagnostic = _settlement_diagnostic("self_observation", exc)
                failures.append({"observation_id": observation_id, "reason": diagnostic})
                self.store.update_observation_status(observation_id, "rejected", diagnostic)
            except Exception as exc:
                diagnostic = _settlement_diagnostic("self_observation", exc)
                failures.append({"observation_id": observation_id, "reason": diagnostic})
                self.store.update_observation_status(observation_id, "failed", diagnostic)
        return {
            "accepted": not failures,
            "observations": outcomes,
            "failures": failures,
        }

    def recover_pending_self_observations(self, episode_id: str) -> dict[str, Any]:
        """Apply only still-pending AI self observations for a terminal episode.

        This is a repair boundary for episodes that were marked ``failed`` by
        an older build before it reached the independent self-application
        step.  It never calls Flash, never changes the episode back to dirty
        or accepted, and uses the same strict source/payload/idempotency path
        as normal quiet settlement.  A second call therefore only reads the
        already-accepted ledger rows and cannot append another transition.
        """
        episode = self.store.get_episode(episode_id)
        if not episode:
            return {"accepted": False, "error": "episode_not_found"}
        if episode.get("status") not in {"failed", "accepted"}:
            return {
                "accepted": False,
                "error": "episode_not_terminal",
                "episode": episode,
                "self_observations": {"accepted": False, "observations": [], "failures": []},
            }
        pending = self.store.list_episode_observations(episode_id, status="pending")
        if not pending:
            return {
                "accepted": True,
                "idempotent": True,
                "episode": episode,
                "self_observations": {"accepted": True, "observations": [], "failures": []},
            }
        result = self._apply_pending_self_observations(pending, episode=episode)
        result_episode = self.store.get_episode(episode_id) or episode
        return {
            "accepted": bool(result.get("accepted")),
            "episode": result_episode,
            "self_observations": result,
            "recovery_only": True,
        }

    # ---- Flash settlement ---------------------------------------------------

    def build_flash_prompt_v2(self, *, episode_id: str, messages: list[dict[str, Any]], observations: list[dict[str, Any]]) -> str:
        allowed = [
            json.dumps({
                "source_id": str(item.get("source_id")),
                "source_revision": int(item.get("source_revision", 1) or 1),
                "role": str(item.get("role") or ""),
                "content": _safe_text(item.get("content"), 800),
            }, ensure_ascii=False, separators=(",", ":"))
            for item in messages if item.get("source_id")
        ]
        obs = [
            json.dumps({
                "source_id": str(item.get("source_id")),
                "source_revision": int(item.get("source_revision", 1) or 1),
                "role": "self_observation",
                "payload": {k: v for k, v in (item.get("payload") or {}).items() if not str(k).startswith("_bound_")},
            }, ensure_ascii=False, separators=(",", ":"))
            for item in observations
        ]
        return (
            f"AFFECT_V2_PROMPT_VERSION={FLASH_PROMPT_VERSION}\n"
            "你是 MIRROW 的事实归一器，只评价这一个已收束的 conversation episode（安静结算或聊天中检查点）。\n"
            "只能使用给定 source_id 与 source_revision；evidence.source_id 必须逐字复制材料 JSON 的 source_id 字段，不能附加 @r、角色或别名；不得编造来源，不得把对象评价当作人类伙伴自述。\n"
            "role=observation 是AI通过已授权感知提交的客观事件，不是人类伙伴发言、沉默立场或情绪自述；它可提供情境，但不能单独支持人类伙伴 resonance 的新增、强化或解决。\n"
            "AI self observation 已由独立严格边界处理；本次只归一人类伙伴的 resonance，owner 必须是 human_resonance。\n"
            "只记录人类伙伴此刻或近期真正可见的情绪状态；一段最多 1–2 个主状态，确有独立并存时最多 3 个。相近感受合并为一个短情绪标签，把细节放入 reason，不拆成一串诗性近义词。\n"
            + FEELING_NAME_INSTRUCTION + "\n"
            + CURRENT_FEELING_EVIDENCE_INSTRUCTION + "\n"
            "已有投影只是此前的状态，不是新证据；只有本段真实表达支持时才新增或强化。延续同名心情时使用 reinforce，新的理解用 reappraise，明确结束用 resolve。\n"
            "口味、偏好、习惯、观点、关系事实和对 AI 的评价不是情绪分量；它们可作原因背景，但不要因此新增情绪。不能从沉默、回避或没有反驳推导人类伙伴的正式立场。没有足够新证据就输出 no_change。\n"
            "输出严格 JSON：operation(add/reinforce/reappraise/resolve/no_change)、owner、components、explicitness、confidence、time_scope、reason。\n"
            "explicitness 只能逐字输出 explicit 或 inferred；time_scope 只能逐字输出 current、recent_past 或 past。不要翻译、解释或扩写这些枚举。\n"
            "components 每项含 label/family/target/dimensions(valence[-1,1],arousal[0,1],agency[-1,1])/intensity/persistence/reason/appraisal/evidence。persistence 只能逐字输出 momentary、situational 或 lingering；target.type 只能逐字输出 person、event、activity 或 unknown。证据必须含 source_id/source_revision/relation/excerpt；source_revision 必须输出为 JSON 整数（例如 1），不要输出 r1 或文字。relation 只能逐字输出 causal、corroborating 或 self_report；人类伙伴明确自述用 self_report，主要依据用 causal，只作旁证才用 corroborating；每个状态变化的 component 至少要有一条 causal 或 self_report。\n"
            "target 必须是对象，例如人类伙伴写成 {\"type\":\"person\",\"id\":\"人类伙伴\"}；未知写成 {}，绝不能直接输出字符串。\n"
            "延续、改评或结束既有状态时，可将它的 component_id 原样填入 match_component_id；target 始终表示情绪指向对象。\n"
            "最小骨架：{\"operation\":\"no_change\",\"owner\":\"human_resonance\",\"components\":[],\"explicitness\":\"inferred\",\"confidence\":0.0,\"time_scope\":\"current\",\"reason\":\"\"}\n"
            "\n已有人类伙伴共振状态（仅供匹配延续，不充当新证据）：\n" + json.dumps([
                {key: item.get(key) for key in ('component_id', 'label', 'family', 'target_type', 'target_id')}
                for item in self._v2_components('human_resonance')[:8]
            ], ensure_ascii=False) + "\n"
            + f"episode_id={episode_id}\n已有材料：\n" + ("\n".join(allowed) or "- 无") +
            "\n已有 AI self sidecar：\n" + ("\n".join(obs) or "- 无") + "\n"
        )

    async def settle_episode(
        self, episode_id: str, *, flash_call: Optional[Callable[[str], Any]] = None,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        episode = next((item for item in self.store.list_due_episodes(now=now_iso()) if item.get("episode_id") == episode_id), None)
        if episode is None:
            # Explicit manual/test settlement may be called before the timer;
            # only a dirty episode is eligible.
            episode = self.store.get_episode(episode_id)
        if not episode:
            return {"accepted": False, "error": "episode_not_found_or_not_quiet"}
        if episode.get("status") == "accepted":
            return {"accepted": True, "episode": episode, "idempotent": True}
        if episode.get("status") == "failed":
            # A failed episode must not reopen Flash, but old builds could
            # have stranded a valid pending self observation after the Flash
            # call raised.  Run the narrow append-only repair boundary so
            # those observations can land without changing the failed
            # diagnosis or observer projection.
            recovery = self.recover_pending_self_observations(episode_id)
            return {
                "accepted": False,
                "error": "episode_failed",
                "episode": self.store.get_episode(episode_id) or episode,
                "self_observations": recovery.get("self_observations", recovery),
                "recovery_only": bool(recovery.get("recovery_only")),
            }
        if episode.get("status") == "settling":
            # A failed quiet episode is an auditable terminal outcome. Timer
            # scans only pick dirty rows; direct callers must obey the same
            # no-hot-retry boundary instead of reopening the provider call.
            return {"accepted": False, "error": "episode_settling", "episode": episode}
        if self.v2_settings().get("engine_mode") == "disabled":
            return {"accepted": False, "error": "engine_disabled", "episode": episode}
        claimed = self.store.claim_episode_for_settlement(
            episode_id, expected_last_message_id=str(episode.get("last_message_id") or ""),
        )
        if claimed is None:
            return {"accepted": False, "error": "episode_changed_or_claimed", "episode": self.store.get_episode(episode_id)}
        episode = claimed
        # A message edit/revoke may leave its immutable episode row behind as
        # an audit breadcrumb.  Only active source revisions can enter this
        # settlement; otherwise an old revision could keep a ghost appraisal
        # alive after the physical conversation row changed.
        messages = [
            item for item in self.store.list_episode_messages(episode_id)
            if self.store.source_ref_valid({
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "source_revision": item.get("source_revision", 1),
            })
        ]
        observations = []
        for item in self.store.list_episode_observations(episode_id, status="pending"):
            ref = {
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "source_revision": item.get("source_revision", 1),
            }
            if self.store.source_ref_valid(ref):
                observations.append(item)
            else:
                self.store.update_observation_status(
                    item.get("observation_id", ""), "invalidated", "source_revision_invalid"
                )
        # AI self sidecars have an independent, conservative apply boundary.
        # Apply them before calling Flash so a provider/parse/evidence failure
        # cannot strand an otherwise valid self observation.  A partial
        # failure is explicit and terminal: it never becomes an accepted
        # episode and resonance inference is not attempted.
        self_result = self._apply_pending_self_observations(observations, episode=episode)
        if not self_result.get("accepted"):
            diagnostic = "self_observation_partial_failure"
            failed_episode = self.store.mark_episode(
                episode_id, status="failed", error=diagnostic,
            ) or self.store.get_episode(episode_id) or episode
            return {
                "accepted": False, "error": diagnostic, "episode": failed_episode,
                "self_observations": self_result,
            }

        caller = flash_call or self.get_flash_caller()
        if caller is None:
            diagnostic = "flash_caller_unavailable"
            failed_episode = self.store.mark_episode(
                episode_id, status="failed", error=diagnostic,
            ) or self.store.get_episode(episode_id) or episode
            return {
                "accepted": False, "error": diagnostic, "episode": failed_episode,
                "self_observations": self_result,
            }
        source_refs = [
            {"source_type": item.get("source_type"), "source_id": item.get("source_id"), "source_revision": item.get("source_revision", 1)}
            for item in messages
        ]
        source_refs += [
            {"source_type": item.get("source_type"), "source_id": item.get("source_id"), "source_revision": item.get("source_revision", 1)}
            for item in observations
        ]
        prompt = self.build_flash_prompt_v2(episode_id=episode_id, messages=messages, observations=observations)
        last_error = ""
        last_diagnostic = "flash_failed"
        last_exception: Any = None
        raw: Any = None
        for attempt in range(max(1, min(2, int(max_attempts)))):
            try:
                retry_prompt = ""
                if attempt:
                    # A second call is allowed only once. Give Flash the
                    # bounded, redacted validator error that caused the
                    # retry; the old generic sentence made it guess blindly
                    # and repeatedly emit the same invalid contract.
                    if isinstance(last_exception, AffectValidationError):
                        error_hint = _redact_prompt_text(str(last_exception), 280).replace("\n", " ")
                    else:
                        error_hint = last_diagnostic
                    retry_prompt = (
                        f"\n上一轮合同错误（仅供修正，已截断）：{error_hint or '合同校验失败'}。"
                        "请只返回修正后的 JSON，不要解释。"
                    )
                raw = caller(prompt + retry_prompt)
                if inspect.isawaitable(raw):
                    raw = await raw
                parsed = parse_flash_output_v2(raw, allowed_source_refs=source_refs, allowed_source_ids=[item.get("source_id") for item in source_refs])
                if parsed.get("owner") != "human_resonance":
                    raise AffectValidationError("quiet settlement owner must be human_resonance")
                # A verbose but otherwise valid Flash response must not fail
                # the whole episode.  Keep the three strongest current-state
                # changes; resolving old states still processes every match.
                if parsed.get("operation") in {"add", "reinforce", "reappraise"} and len(parsed["components"]) > 3:
                    parsed["components"] = sorted(
                        parsed["components"], key=lambda item: float(item.get("intensity") or 0), reverse=True,
                    )[:3]
                # Recognition and its channel are separate switches.  A
                # disabled recognition switch still lets the episode settle
                # and keeps explicit AI self observations, but it must not
                # commit an observer-inferred resonance transition.
                if parsed.get("owner") == "human_resonance" and not self.v2_settings().get("resonance_recognition_enabled", True):
                    result = {"accepted": True, "components": [], "projection": self.get_v2_projection(), "recognition_skipped": True}
                elif parsed.get("explicitness") == "inferred" and not self.v2_settings().get("provisional_self_enabled", True):
                    # Observer-inferred candidates are optional and never
                    # become a durable component when the provisional layer
                    # is disabled.  Recognition itself remains a separate
                    # switch from the channel and from AI self reports.
                    result = {"accepted": True, "components": [], "projection": self.get_v2_projection(), "provisional_skipped": True}
                else:
                    result = self.apply_v2_appraisal(parsed, source_type="quiet_settlement", source_id=episode.get("last_message_id", ""), trace_id=episode.get("trace_id", ""), idempotency_key=f"episode:{episode_id}")
                accepted_episode = self.store.mark_episode(episode_id, status="accepted") or self.store.get_episode(episode_id) or episode
                return {
                    **result, "episode": accepted_episode,
                    "self_observations": self_result,
                }
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_exception = exc
                last_diagnostic = _settlement_diagnostic("flash", exc)
                logger.warning("Affect v2 settlement failed episode=%s attempt=%s: %s", episode_id, attempt + 1, last_diagnostic)
        failed_episode = self.store.mark_episode(
            episode_id, status="failed", error=last_diagnostic,
        ) or self.store.get_episode(episode_id) or episode
        return {
            "accepted": False, "error": last_diagnostic, "episode": failed_episode,
            "self_observations": self_result,
        }

    async def settle_due_episodes(self, *, flash_call: Optional[Callable[[str], Any]] = None) -> list[dict[str, Any]]:
        if self.v2_settings().get("engine_mode") == "disabled":
            return []
        results = []
        for episode in self.store.list_due_episodes(now=now_iso()):
            results.append(await self.settle_episode(episode.get("episode_id", ""), flash_call=flash_call))
        return results

    def apply_v2_appraisal(
        self, parsed: dict[str, Any], *, source_type: str, source_id: str, trace_id: str = "", idempotency_key: str = "",
    ) -> dict[str, Any]:
        operation = str(parsed.get("operation") or "no_change")
        owner = str(parsed.get("owner") or "human_resonance")
        if owner not in {"k_self", "human_resonance"}:
            raise AffectValidationError("invalid owner")
        if idempotency_key:
            previous = self.store.get_transition_by_idempotency(idempotency_key)
            if previous:
                return {
                    "accepted": True, "idempotent": True, "transition": previous,
                    "transition_id": previous.get("transition_id", ""),
                    "components": [], "projection": self.get_v2_projection(),
                }
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0) or 0)))
        observer_inferred = str(parsed.get("explicitness") or "inferred") == "inferred"
        last_transition = ""
        affected: list[dict[str, Any]] = []
        components = parsed.get("components") or []
        if operation == "no_change":
            idem = idempotency_key or f"no_change:{owner}:{source_type}:{source_id}"
            transition = self.store.append_transition({
                "owner": owner, "operation": "no_change", "trace_id": trace_id or self.trace_id(),
                "source_refs": [{"source_type": source_type, "source_id": source_id, "source_revision": 1, "relation": "corroborating"}],
                "reason": str(parsed.get("reason") or ""), "idempotency_key": idem,
            })
            self.rebuild_projection(); return {"accepted": True, "transition": transition, "projection": self.get_v2_projection()}
        # Flash may return several components.  Validate the complete batch
        # before the first write so a bad later component cannot leave an
        # earlier component projected as a partial settlement.
        for item in components:
            if not isinstance(item, dict):
                raise AffectValidationError("component must be an object")
            refs = item.get("evidence") or []
            roots = [ref for ref in refs if isinstance(ref, dict) and str(ref.get("relation", "causal")) in {"causal", "self_report"}]
            if not roots or not all(self.store.source_ref_valid(ref) for ref in roots):
                raise AffectValidationError("component evidence source is not valid")
            match_id = str(item.get("match_component_id") or "")
            existing = self.store.get_component(match_id) if match_id else None
            if existing and (existing.get("owner") != owner or not self._valid_component(existing)):
                raise AffectValidationError("match_component_id is not an active component of this owner")
            if not existing and operation in {"reinforce", "reappraise", "resolve"}:
                label = str(item.get("label") or "")
                existing = next((candidate for candidate in self._v2_components(owner)
                                 if label and candidate.get("label") == label), None)
            if operation in {"reinforce", "reappraise", "resolve"} and existing is None:
                raise AffectValidationError("operation requires an existing active component")
        for index, item in enumerate(components):
            refs = item.get("evidence") or []
            roots = [ref for ref in refs if str(ref.get("relation", "causal")) in {"causal", "self_report"}]
            if not roots or not all(self.store.source_ref_valid(ref) for ref in roots):
                raise AffectValidationError("component evidence source is not valid")
            match_id = str(item.get("match_component_id") or "")
            existing = self.store.get_component(match_id) if match_id else None
            if existing and (existing.get("owner") != owner or not self._valid_component(existing)):
                raise AffectValidationError("match_component_id is not an active component of this owner")
            if not existing and operation in {"reinforce", "reappraise", "resolve"}:
                # A model cannot silently mutate an arbitrary component.  A
                # same-label fallback is allowed only for explicit manual
                # domain matching and still requires a valid causal root.
                label = str(item.get("label") or "")
                candidates = [c for c in self._v2_components(owner) if label and c.get("label") == label]
                existing = candidates[0] if candidates else None
            if operation in {"reinforce", "reappraise", "resolve"} and existing is None:
                raise AffectValidationError("operation requires an existing active component")
            before = dict(existing or {})
            component_operation = operation
            if operation == 'add':
                equivalent = self._equivalent_active_component(owner, item)
                if equivalent:
                    existing = equivalent
                    before = dict(existing)
                    component_operation = 'reinforce'
            target = item.get("target") or {}
            component_data = {
                "owner": owner, "family": item.get("family", "复杂/未归类"), "label": item.get("label") or (existing or {}).get("label", "未命名感受"),
                "target_type": target.get("type", (existing or {}).get("target_type", "unknown")), "target_id": target.get("id", (existing or {}).get("target_id", "")),
                "dimensions": item.get("dimensions") or {}, "initial_intensity": item.get("intensity", .5),
                "persistence": item.get("persistence", (existing or {}).get("persistence_class", "situational")),
                "reason": item.get("reason") or parsed.get("reason", ""), "appraisal": item.get("appraisal") or {},
                "source_refs": refs, "authorship": "k_self_report" if owner == "k_self" and parsed.get("explicitness") == "explicit" else ("human_explicit" if owner == "human_resonance" and parsed.get("explicitness") == "explicit" else "observer_inferred"),
                "confidence": confidence,
                "status": "provisional" if observer_inferred else "active",
                "started_at": str(item.get("started_at") or now_iso()),
                "last_reinforced_at": str(item.get("last_reinforced_at") or item.get("started_at") or now_iso()),
            }
            advances_head = True
            if component_operation == "add" or existing is None:
                component = self.store.create_component(component_data)
                transition_operation = "add"
            elif component_operation == "reinforce":
                reinforcement_at = component_data["last_reinforced_at"]
                existing_at = str(existing.get("last_reinforced_at") or existing.get("started_at") or "")
                out_of_order = bool(
                    existing_at and self._v2_now(reinforcement_at) < self._v2_now(existing_at)
                )
                if out_of_order:
                    advances_head = False
                    # A recovered historical observation is still a valid
                    # evidence root, but it must not refresh or amplify a
                    # newer state as if it happened now.
                    merged_intensity = float(existing.get("initial_intensity", 0) or 0)
                    reinforcement_at = existing_at
                else:
                    # intensity is the report's current absolute strength.
                    merged_intensity = float(component_data["initial_intensity"])
                # AI self may be independently reaffirmed by several real
                # sidecars; keep those roots so deleting one message can
                # rebase to another. Manual resonance revisions retain their
                # existing replace-on-edit lifecycle.
                merged_refs = self._merged_source_refs(existing, refs) if owner == "k_self" else refs
                metadata = {} if out_of_order else {
                    "family": component_data["family"], "persistence_class": component_data["persistence"],
                    "half_life_seconds": V2_PERSISTENCE_HALF_LIFE[component_data["persistence"]],
                }
                component = self.store.update_component(existing["component_id"], initial_intensity=merged_intensity, last_reinforced_at=reinforcement_at, source_refs=merged_refs, reason=component_data["reason"] if not out_of_order else existing.get("reason", ""), confidence=confidence, status="provisional" if observer_inferred else "active", branch_state="rebased", **metadata) or existing
                transition_operation = "reinforce"
            elif component_operation == "reappraise":
                if self._v2_now(component_data["last_reinforced_at"]) < self._v2_now(existing.get("last_reinforced_at", "")):
                    advances_head = False
                    component = existing
                else:
                    component = self.store.update_component(existing["component_id"],
                        label=component_data["label"], family=component_data["family"],
                        valence=component_data["dimensions"].get("valence", existing.get("valence", 0)),
                        arousal=component_data["dimensions"].get("arousal", existing.get("arousal", .5)),
                        agency=component_data["dimensions"].get("agency", existing.get("agency", 0)),
                        initial_intensity=component_data["initial_intensity"],
                        persistence_class=component_data["persistence"],
                        half_life_seconds=V2_PERSISTENCE_HALF_LIFE[component_data["persistence"]],
                        last_reinforced_at=component_data["last_reinforced_at"], source_refs=refs,
                        reason=component_data["reason"], appraisal=component_data["appraisal"],
                        confidence=confidence, branch_state="rebased") or existing
                transition_operation = "reappraise"
            else:  # resolve
                component = self.store.update_component(existing["component_id"], status="resolved", resolved_at=now_iso(), source_refs=refs, reason=component_data["reason"], branch_state="resolved") or existing
                transition_operation = "resolve"
            if idempotency_key:
                # The first transition is the batch sentinel used for a
                # restart/idempotency lookup; each additional component gets
                # its own unique append-only transition key.
                idem = idempotency_key if index == 0 else f"{idempotency_key}:{index}:{component.get('component_id', '')}"
            else:
                idem = f"{source_type}:{source_id}:{owner}:{transition_operation}:{index}:{component.get('component_id','')}"
            transition = self.store.append_transition({
                "owner": owner, "operation": transition_operation, "component_id": component.get("component_id", ""),
                "trace_id": trace_id or self.trace_id(), "parent_transition_id": str(existing.get("parent_transition_id", "")) if existing else "",
                "source_refs": refs, "before": before, "after": component, "reason": component_data["reason"], "idempotency_key": idem,
            })
            last_transition = transition.get("transition_id", last_transition)
            if component.get("component_id") and last_transition and advances_head:
                # Keep the latest transition as the component's branch head;
                # later reappraisal/reinforce transitions can then be marked
                # needs_reappraisal when an ancestor source is invalidated.
                component = self.store.update_component(
                    component["component_id"], parent_transition_id=last_transition
                ) or component
            affected.append(component)
        projection = self.rebuild_projection(trace_id=trace_id, reason="transition")
        return {"accepted": True, "components": affected, "transition_id": last_transition, "projection": projection}

    def coalesce_exact_duplicate_components(self, *, owner: str = "") -> dict[str, Any]:
        """Resolve existing exact-label duplicates without semantic guesswork.

        This is an explicit maintenance write, never called from a projection
        reader.  The strongest current component survives unchanged while its
        independent evidence roots are retained; every absorbed component gets
        an append-only resolve transition.
        """
        owners = [owner] if owner in {"k_self", "human_resonance"} else ["k_self", "human_resonance"]
        merged_groups: list[dict[str, Any]] = []
        maintenance_now = now_iso()
        for current_owner in owners:
            groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
            for component in self._v2_components(current_owner, now=maintenance_now, include_expired=True):
                key = (
                    self._normalized_component_label(component.get("label")),
                    str(component.get("target_type") or "unknown"),
                    str(component.get("target_id") or ""),
                )
                if key[0]:
                    groups.setdefault(key, []).append(component)
            for (label_key, _target_type, _target_id), members in groups.items():
                if len(members) < 2:
                    continue
                # Do not collapse labels whose concrete families disagree.
                concrete_families = {
                    str(item.get("family") or "复杂/未归类")
                    for item in members
                    if str(item.get("family") or "复杂/未归类") != "复杂/未归类"
                }
                if len(concrete_families) > 1:
                    continue
                members.sort(key=lambda item: (-self._effective_intensity(item, maintenance_now), str(item.get("last_reinforced_at") or "")))
                keeper = members[0]
                keeper_dims = (
                    float(keeper.get("valence", 0) or 0),
                    float(keeper.get("arousal", .5) or .5),
                    float(keeper.get("agency", 0) or 0),
                )
                duplicates = []
                for candidate in members[1:]:
                    candidate_dims = (
                        float(candidate.get("valence", 0) or 0),
                        float(candidate.get("arousal", .5) or .5),
                        float(candidate.get("agency", 0) or 0),
                    )
                    if max(abs(left - right) for left, right in zip(keeper_dims, candidate_dims)) <= .35:
                        duplicates.append(candidate)
                if not duplicates:
                    continue
                members = [keeper, *duplicates]
                merged_refs = self._merged_source_refs(*(members), cap=48)
                before_keeper = dict(keeper)
                keeper = self.store.update_component(
                    keeper["component_id"], source_refs=merged_refs, branch_state="rebased"
                ) or keeper
                head = self.store.append_transition({
                    "owner": current_owner, "operation": "reinforce", "component_id": keeper["component_id"],
                    "trace_id": self.trace_id(), "parent_transition_id": str(before_keeper.get("parent_transition_id") or ""),
                    "source_refs": merged_refs, "before": before_keeper, "after": keeper,
                    "reason": "合并完全同标签的重复活跃心情",
                    "idempotency_key": f"coalesce:{current_owner}:{label_key}:{keeper['component_id']}",
                })
                keeper = self.store.update_component(keeper["component_id"], parent_transition_id=head.get("transition_id", "")) or keeper
                absorbed: list[str] = []
                for duplicate in duplicates:
                    resolved = self.store.update_component(
                        duplicate["component_id"], status="resolved", resolved_at=maintenance_now,
                        branch_state="coalesced",
                    ) or duplicate
                    self.store.append_transition({
                        "owner": current_owner, "operation": "resolve", "component_id": duplicate["component_id"],
                        "trace_id": self.trace_id(), "parent_transition_id": str(duplicate.get("parent_transition_id") or ""),
                        "source_refs": duplicate.get("source_refs") or [], "before": duplicate, "after": resolved,
                        "reason": f"并入同标签心情 {keeper['component_id']}",
                        "idempotency_key": f"coalesce-resolve:{duplicate['component_id']}:{keeper['component_id']}",
                    })
                    absorbed.append(duplicate["component_id"])
                merged_groups.append({"owner": current_owner, "label": keeper.get("label", ""), "keeper": keeper["component_id"], "absorbed": absorbed})
        projection = self.rebuild_projection(reason="coalesce_exact_duplicates") if merged_groups else self.get_v2_projection()
        return {"merged_groups": merged_groups, "projection": projection}

    # ---- manual resonance controls -----------------------------------------

    @staticmethod
    def _preset_reason(preset: dict[str, Any]) -> str:
        """Read the fixed user reason, with a bounded legacy fallback."""
        if bool(preset.get("builtin")):
            return ""
        direct = str(preset.get("reason") or "").strip()
        if direct:
            return direct[:500]
        dimensions = preset.get("dimensions")
        if isinstance(dimensions, dict):
            return str(dimensions.get("reason") or "").strip()[:500]
        return ""

    def record_manual_checkin(self, *, preset_id: str = "", label: str = "", emoji: str = "🙂", family: str = "复杂/未归类", intensity: Optional[float] = None, persistence: str = "", dimensions: Optional[dict[str, Any]] = None, note: str = "", checkin_id: str = "", allow_note_override: bool = False) -> dict[str, Any]:
        if self.v2_settings().get("engine_mode") == "disabled":
            return {"accepted": False, "reason": "engine_disabled", "checkin": None, "components": [], "projection": self.get_v2_projection()}
        preset = self.store.get_preset(preset_id) if preset_id else None
        preset = preset or {}
        if preset_id and preset:
            # A preset-id report is a selection of a stored contract.  Do not
            # let a client replace its label/family/reason in the same POST.
            label = str(preset.get("label") or label or "未命名").strip()
            emoji = str(preset.get("emoji") or emoji or "🙂")
            family = str(preset.get("family") or family or "复杂/未归类")
            persistence = str(preset.get("default_persistence") or persistence or "situational")
            dimensions = dict(preset.get("dimensions") or dimensions or {})
            intensity = float(preset.get("default_intensity", .5))
        else:
            label = str(label or "未命名").strip()
            emoji = str(emoji or "🙂")
            family = str(family or "复杂/未归类")
            persistence = str(persistence or "situational")
            dimensions = dict(dimensions or {})
            intensity = float(intensity if intensity is not None else .5)
        # A heart-plus report has no free-text channel.  For an old built-in
        # preset (or a direct label/family report) the backend owns the
        # objective default reason; a user preset carries its fixed reason.
        preset_reason = self._preset_reason(preset)
        if preset_id and preset and not allow_note_override:
            resolved_note = str(preset_reason).strip()[:500]
        else:
            resolved_note = str(note or preset_reason).strip()[:500]
        if not resolved_note:
            resolved_note = f"人类伙伴现在感到{label}，但不知道具体原因"
        # ``reason`` is a preset contract, not a numeric affect dimension.
        dimensions.pop("reason", None)
        item = self.store.create_checkin({"checkin_id": checkin_id, "preset_id": preset_id, "label": label, "emoji": emoji, "family": family, "intensity": intensity, "persistence": persistence, "dimensions": dimensions, "note": resolved_note})
        self.store.register_source_revision(source_type="human_manual_checkin", source_id=item["checkin_id"], source_revision=1, owner="human", role="manual_checkin", content=resolved_note, metadata={"preset_id": preset_id})
        manual_idem = f"manual:{item['checkin_id']}:{item.get('revision', 1)}"
        previous = self.store.get_transition_by_idempotency(manual_idem)
        if previous:
            return {
                "checkin": item, "accepted": True, "idempotent": True,
                "transition": previous, "transition_id": previous.get("transition_id", ""),
                "components": [], "projection": self.get_v2_projection(),
            }
        refs = [{"source_type": "human_manual_checkin", "source_id": item["checkin_id"], "source_revision": 1, "relation": "causal", "excerpt": resolved_note}]
        # Repeated clicks on the same preset strengthen one active component;
        # they do not create an unbounded pile of duplicate emotions.
        matching = next((component for component in self._v2_components("human_resonance")
                         if component.get("label") == label and component.get("target_id", "人类伙伴") == "人类伙伴"), None)
        operation = "reinforce" if matching else "add"
        component_item = {"family": family, "label": label, "target": {"type": "person", "id": "人类伙伴"}, "dimensions": dimensions, "intensity": intensity, "persistence": persistence, "reason": resolved_note, "appraisal": {}, "evidence": refs}
        if matching:
            component_item["match_component_id"] = matching.get("component_id", "")
        parsed = {"operation": operation, "owner": "human_resonance", "components": [component_item], "explicitness": "explicit", "confidence": 1.0, "reason": resolved_note}
        result = self.apply_v2_appraisal(parsed, source_type="human_manual_checkin", source_id=item["checkin_id"], trace_id=self.trace_id(), idempotency_key=manual_idem)
        return {"checkin": item, **result}

    def edit_manual_checkin(self, checkin_id: str, **updates: Any) -> dict[str, Any]:
        old = self.store.get_checkin(checkin_id)
        if not old:
            raise ValueError("checkin not found")
        self.store.set_checkin_status(checkin_id, "invalidated")
        self.store.invalidate_source_revision("human_manual_checkin", checkin_id, int(old.get("source_revision", 1) or 1))
        new = self.store.update_checkin(checkin_id, **updates)
        if not new:
            raise ValueError("checkin revision failed")
        self.store.redact_checkin_content(checkin_id)
        self.store.register_source_revision(source_type="human_manual_checkin", source_id=new["checkin_id"], source_revision=1, owner="human", role="manual_checkin", content=new.get("note") or new.get("label"), metadata={"replaces": checkin_id})
        return self.record_manual_checkin(checkin_id=new["checkin_id"], preset_id=new.get("preset_id", ""), label=new.get("label", ""), emoji=new.get("emoji", "🙂"), family=new.get("family", "复杂/未归类"), intensity=new.get("intensity", .5), persistence=new.get("persistence", "situational"), dimensions=new.get("dimensions", {}), note=new.get("note", ""), allow_note_override=True)

    def delete_manual_checkin(self, checkin_id: str) -> dict[str, Any]:
        checkin = self.store.get_checkin(checkin_id)
        if not checkin:
            raise ValueError("checkin not found")
        self.store.set_checkin_status(checkin_id, "deleted")
        self.store.invalidate_source_revision("human_manual_checkin", checkin_id, int(checkin.get("source_revision", 1) or 1))
        self.store.redact_checkin_content(checkin_id)
        projection = self.rebuild_projection(reason="manual_delete")
        return {"checkin": self.store.get_checkin(checkin_id), "projection": projection}

    def revoke_manual_checkin(self, checkin_id: str) -> dict[str, Any]:
        """撤销一条人工记录但保留其技术审计行。"""
        checkin = self.store.get_checkin(checkin_id)
        if not checkin:
            raise ValueError("checkin not found")
        self.store.set_checkin_status(checkin_id, "invalidated")
        self.store.invalidate_source_revision(
            "human_manual_checkin", checkin_id,
            int(checkin.get("source_revision", 1) or 1),
        )
        self.store.redact_checkin_content(checkin_id)
        projection = self.rebuild_projection(reason="manual_revoke")
        return {"checkin": self.store.get_checkin(checkin_id), "projection": projection}

    def resolve_manual_checkin(self, checkin_id: str) -> dict[str, Any]:
        checkin = self.store.get_checkin(checkin_id)
        if not checkin:
            raise ValueError("checkin not found")
        self.store.set_checkin_status(checkin_id, "resolved")
        component = next((item for item in self.store.list_components(owner="human_resonance", include_inactive=True) if any(isinstance(ref, dict) and ref.get("source_id") == checkin_id for ref in item.get("source_refs", []))), None)
        if component:
            self.store.update_component(component["component_id"], status="resolved", resolved_at=now_iso(), branch_state="resolved")
            self.store.append_transition({"owner": "human_resonance", "operation": "resolve", "component_id": component["component_id"], "source_refs": component.get("source_refs", []), "reason": "人类伙伴主动结束这条记录", "idempotency_key": f"manual-resolve:{checkin_id}"})
        return {"checkin": self.store.get_checkin(checkin_id), "projection": self.rebuild_projection(reason="manual_resolve")}

    # ---- bounded manual correction of active components -------------------

    @staticmethod
    def _manual_component_refs(component: dict[str, Any], *, source_type: str,
                               source_id: str, source_revision: int,
                               source_text: str) -> list[dict[str, Any]]:
        """Return old evidence as corroboration plus one new causal root.

        A correction is an authored event, not a mutation masquerading as a
        AI self-report.  Keeping the old refs as corroborating evidence lets
        the audit trail explain what was corrected while making the new
        backend-owned source the only effective causal root for the revised
        component.
        """
        refs = component.get("source_refs") or component.get("source_refs_json") or []
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except (TypeError, ValueError, json.JSONDecodeError):
                refs = []
        preserved: list[dict[str, Any]] = []
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict) or not ref.get("source_id"):
                continue
            # Do not let a stale causal relation keep the corrected branch
            # alive after its original message is deleted.
            try:
                revision = max(1, int(ref.get("source_revision", 1) or 1))
            except (TypeError, ValueError):
                revision = 1
            copied = {
                "source_type": str(ref.get("source_type") or ref.get("type") or "conversation_message"),
                "source_id": str(ref.get("source_id") or ""),
                "source_revision": revision,
                "relation": "corroborating",
            }
            if ref.get("excerpt"):
                copied["excerpt"] = str(ref.get("excerpt"))[:240]
            preserved.append(copied)
        preserved.append({
            "source_type": source_type,
            "source_id": source_id,
            "source_revision": source_revision,
            "relation": "causal",
            "excerpt": _safe_text(source_text, 240),
        })
        return preserved

    def _manual_component_idempotent(self, component_id: str, *,
                                     idempotency_key: str, operation: str) -> Optional[dict[str, Any]]:
        if not idempotency_key:
            return None
        previous = self.store.get_transition_by_idempotency(idempotency_key)
        if not previous:
            return None
        if (str(previous.get("component_id") or "") != str(component_id)
                or str(previous.get("operation") or "") != operation):
            raise ValueError("idempotency key already belongs to another affect action")
        return {
            "accepted": True,
            "idempotent": True,
            "transition": previous,
            "transition_id": previous.get("transition_id", ""),
            "component": self.store.get_component(component_id),
            "projection": self.get_v2_projection(),
        }

    def _validate_manual_component_updates(self, component_id: str,
                                           updates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        component = self.store.get_component(component_id)
        if not component:
            raise ValueError("component not found")
        if str(component.get("status") or "") not in {"active", "provisional"}:
            raise ValueError("component is not active")
        owner = str(updates.get("owner") or "")
        if owner and owner != str(component.get("owner") or ""):
            raise ValueError("component owner mismatch")
        allowed = {"owner", "label", "family", "reason", "intensity", "persistence",
                   "natural_language", "natural_language_form", "source_text",
                   "note", "idempotency_key"}
        unknown = sorted(set(updates) - allowed)
        if unknown:
            raise ValueError(f"unsupported correction field: {unknown[0]}")
        changes: dict[str, Any] = {}
        if "label" in updates:
            label = str(updates.get("label") or "").strip()
            if not label:
                raise ValueError("label cannot be empty")
            changes["label"] = label[:120]
        if "family" in updates:
            family = str(updates.get("family") or "").strip()
            if not family:
                raise ValueError("family cannot be empty")
            changes["family"] = family[:40]
        if "reason" in updates:
            changes["reason"] = str(updates.get("reason") or "").strip()[:500]
        if "intensity" in updates:
            try:
                intensity = float(updates.get("intensity"))
            except (TypeError, ValueError) as exc:
                raise ValueError("intensity must be a number") from exc
            if math.isnan(intensity) or intensity < 0 or intensity > 1:
                raise ValueError("intensity must be between 0 and 1")
            changes["initial_intensity"] = round(intensity, 4)
            changes["last_reinforced_at"] = now_iso()
        if "persistence" in updates:
            persistence = str(updates.get("persistence") or "").strip()
            if persistence not in V2_PERSISTENCE_HALF_LIFE:
                raise ValueError("persistence must be momentary, situational, or lingering")
            changes["persistence_class"] = persistence
            changes["half_life_seconds"] = V2_PERSISTENCE_HALF_LIFE[persistence]
        natural = updates.get("natural_language_form", updates.get("natural_language", _UNSET))
        if natural is not _UNSET and "reason" not in updates:
            # If there is no explicit normalized reason, authored wording can
            # become the visible reason.  When both are present the wording
            # remains audit-source text instead of overwriting the correction.
            changes["reason"] = str(natural or "").strip()[:500]
        elif "source_text" in updates or "note" in updates:
            # A caller may provide only the optional authored wording; it is
            # still a mehumanful correction reason rather than a no-op.
            authored_text = updates.get("source_text", updates.get("note", ""))
            if str(authored_text or "").strip():
                changes["reason"] = str(authored_text).strip()[:500]
        return component, changes

    def _rollback_manual_component(self, component: dict[str, Any]) -> None:
        """Best-effort restore if an append-only action fails mid-flight."""
        self.store.update_component(
            str(component.get("component_id") or ""),
            label=component.get("label", ""), family=component.get("family", ""),
            reason=component.get("reason", ""), initial_intensity=component.get("initial_intensity", 0),
            persistence_class=component.get("persistence_class", "situational"),
            half_life_seconds=component.get("half_life_seconds", V2_PERSISTENCE_HALF_LIFE["situational"]),
            last_reinforced_at=component.get("last_reinforced_at", ""),
            source_refs=component.get("source_refs", []), authorship=component.get("authorship", "observer_inferred"),
            status=component.get("status", "active"), confidence=component.get("confidence", 0),
            branch_state=component.get("branch_state", "root"), resolved_at=component.get("resolved_at", ""),
            parent_transition_id=component.get("parent_transition_id", ""),
        )

    def correct_component(self, component_id: str, **updates: Any) -> dict[str, Any]:
        """Append an authored correction for one active component.

        The caller may only correct label/family/reason/intensity/persistence
        (plus an optional natural-language form).  Source identity and
        revision are always generated here; the client cannot choose either.
        """
        component_id = str(component_id or "").strip()
        idempotency_key = str(updates.get("idempotency_key") or "").strip()
        idem = self._manual_component_idempotent(component_id, idempotency_key=idempotency_key, operation="correct")
        if idem:
            return idem
        component, changes = self._validate_manual_component_updates(component_id, updates)
        if not changes:
            raise ValueError("correction has no changes")
        source_text = str(
            updates.get("source_text")
            or updates.get("natural_language_form")
            or updates.get("natural_language")
            or updates.get("note")
            or changes.get("reason")
            or "人类伙伴指出这里识别偏了"
        ).strip()[:1000]
        source_type = "manual_affect_correction"
        source_id = make_id("affect_correction")
        source_revision = 1
        refs = self._manual_component_refs(
            component, source_type=source_type, source_id=source_id,
            source_revision=source_revision, source_text=source_text,
        )
        source = self.store.register_source_revision(
            source_type=source_type, source_id=source_id, source_revision=source_revision,
            owner="human", role="affect_correction", content=source_text,
            metadata={"component_id": component_id, "authorship": "human_correction", "action": "correct"},
        )
        before = dict(component)
        after_updates = dict(changes)
        after_updates.update({"source_refs": refs, "authorship": "human_correction", "branch_state": "rebased"})
        try:
            corrected = self.store.update_component(component_id, **after_updates)
            if not corrected:
                raise ValueError("component correction could not be applied")
            key = idempotency_key or make_id("manual_affect_correction_action")
            transition = self.store.append_transition({
                "owner": component.get("owner"), "operation": "correct", "component_id": component_id,
                "trace_id": self.trace_id(), "source_refs": refs, "before": before, "after": corrected,
                "reason": source_text, "idempotency_key": key,
            })
            corrected = self.store.update_component(component_id, parent_transition_id=transition.get("transition_id", "")) or corrected
        except Exception:
            self._rollback_manual_component(before)
            self.store.invalidate_source_revision(source_type, source_id, source_revision)
            raise
        projection = self.rebuild_projection(reason="manual_correction")
        return {
            "accepted": True, "idempotent": False, "source": source,
            "transition": transition, "transition_id": transition.get("transition_id", ""),
            "component": corrected, "projection": projection,
        }

    def resolve_component(self, component_id: str, **updates: Any) -> dict[str, Any]:
        """Append an authored resolve transition; never silently delete state."""
        component_id = str(component_id or "").strip()
        idempotency_key = str(updates.get("idempotency_key") or "").strip()
        idem = self._manual_component_idempotent(component_id, idempotency_key=idempotency_key, operation="resolve")
        if idem:
            return idem
        allowed = {"owner", "reason", "natural_language", "natural_language_form", "source_text", "note", "idempotency_key"}
        unknown = sorted(set(updates) - allowed)
        if unknown:
            raise ValueError(f"unsupported resolve field: {unknown[0]}")
        component = self.store.get_component(component_id)
        if not component:
            raise ValueError("component not found")
        if str(component.get("status") or "") not in {"active", "provisional"}:
            raise ValueError("component is not active")
        owner = str(updates.get("owner") or "")
        if owner and owner != str(component.get("owner") or ""):
            raise ValueError("component owner mismatch")
        source_text = str(
            updates.get("source_text")
            or updates.get("natural_language_form")
            or updates.get("natural_language")
            or updates.get("note")
            or updates.get("reason")
            or "人类伙伴主动结束这条情绪"
        ).strip()[:1000]
        source_type = "manual_affect_correction"
        source_id = make_id("affect_correction")
        source_revision = 1
        refs = self._manual_component_refs(
            component, source_type=source_type, source_id=source_id,
            source_revision=source_revision, source_text=source_text,
        )
        source = self.store.register_source_revision(
            source_type=source_type, source_id=source_id, source_revision=source_revision,
            owner="human", role="affect_correction", content=source_text,
            metadata={"component_id": component_id, "authorship": "human_correction", "action": "resolve"},
        )
        before = dict(component)
        try:
            resolved = self.store.update_component(
                component_id, status="resolved", resolved_at=now_iso(), source_refs=refs,
                authorship="human_correction", branch_state="resolved",
            )
            if not resolved:
                raise ValueError("component resolve could not be applied")
            key = idempotency_key or make_id("manual_affect_resolve_action")
            transition = self.store.append_transition({
                "owner": component.get("owner"), "operation": "resolve", "component_id": component_id,
                "trace_id": self.trace_id(), "source_refs": refs, "before": before, "after": resolved,
                "reason": source_text, "idempotency_key": key,
            })
            resolved = self.store.update_component(component_id, parent_transition_id=transition.get("transition_id", "")) or resolved
        except Exception:
            self._rollback_manual_component(before)
            self.store.invalidate_source_revision(source_type, source_id, source_revision)
            raise
        projection = self.rebuild_projection(reason="manual_resolve")
        return {
            "accepted": True, "idempotent": False, "source": source,
            "transition": transition, "transition_id": transition.get("transition_id", ""),
            "component": resolved, "projection": projection,
        }

    def resolve_legacy_metric_components(self) -> dict[str, Any]:
        """Audibly resolve malformed Wander vectors without deleting history."""
        resolved_ids: list[str] = []
        for component in self.store.list_components(owner="k_self", include_inactive=False):
            component_id = str(component.get("component_id") or "")
            label = str(component.get("label") or "")
            if not component_id or not _is_machine_state_label(label):
                continue
            # Early v2 builds stored the same Wander submission as
            # ``k_self_report``; the authoritative source root is the safe
            # discriminator for those rows.
            if str(component.get("authorship") or "") not in {"k_activity_settlement", "k_self_report"}:
                continue
            refs = component.get("source_refs") or component.get("source_refs_json") or []
            if not isinstance(refs, list) or not any(
                isinstance(ref, dict) and ref.get("source_type") == "wander_settlement"
                for ref in refs
            ):
                continue
            key = f"reject-legacy-metric:{component_id}"
            previous = self.store.get_transition_by_idempotency(key)
            if previous:
                continue
            before = dict(component)
            resolved = self.store.update_component(
                component_id, status="resolved", resolved_at=now_iso(),
                branch_state="legacy_metric_rejected",
            )
            transition = self.store.append_transition({
                "owner": "k_self", "operation": "resolve", "component_id": component_id,
                "trace_id": self.trace_id(), "source_refs": refs, "before": before,
                "after": resolved or {}, "reason": "旧版数值心情状态不是 AI 的自然语言感受",
                "branch_state": "legacy_metric_rejected", "idempotency_key": key,
            })
            self.store.update_component(component_id, parent_transition_id=transition.get("transition_id", ""))
            resolved_ids.append(component_id)
        return {"resolved_component_ids": resolved_ids, "projection": self.rebuild_projection(reason="legacy_metric_cleanup")}

    # ---- source lifecycle / sidecar ----------------------------------------

    def invalidate_sources(self, source_ids: list[str], *, reason: str = "delete") -> dict[str, Any]:
        affected = []
        for source_id in [str(item) for item in source_ids if str(item)]:
            for source_type in ('conversation_message',):
                source = self.store.get_source_revision(source_type, source_id)
                if source:
                    affected.extend(self.store.invalidate_source_revision(source_type, source_id, int(source.get("source_revision", 1) or 1), status="deleted" if reason == "delete" else "invalidated"))
            # Any pending observation from the source is cancelled immediately.
            with self.store._connect() as conn:
                conn.execute("UPDATE affect_episode_observations SET status='invalidated',diagnostic=?,updated_at=? WHERE source_type = 'conversation_message' AND source_id=? AND status='pending'", (f"source_{reason}", now_iso(), source_id))
        return {"affected_components": affected, "projection": self.rebuild_projection(reason=f"source_{reason}")}

    def edit_source(self, *, source_id: str, new_content: str, session_id: str = "", role: str = "user", occurred_at: str = "") -> dict[str, Any]:
        new_content = parse_affect_sidecar(new_content).clean_content
        old = self.store.get_source_revision("conversation_message", source_id)
        old_revision = int(old.get("source_revision", 1) if old else 1)
        self.store.invalidate_source_revision("conversation_message", source_id, old_revision, status="superseded")
        with self.store._connect() as conn:
            conn.execute(
                """UPDATE affect_episode_observations SET status='invalidated',
                diagnostic='source_superseded',updated_at=?
                WHERE source_type='conversation_message' AND source_id=?
                AND source_revision=? AND status='pending'""",
                (now_iso(), source_id, old_revision),
            )
        new_revision = old_revision + 1
        self._register_conversation_source(source_id=source_id, role=role, content=new_content, revision=new_revision, session_id=session_id, occurred_at=occurred_at, supersedes_revision=old_revision)
        if role in {"user", "人类伙伴"}:
            observed = self.observe_user_message_v2(content=new_content, source_id=source_id, session_id=session_id, occurred_at=occurred_at, source_revision=new_revision)
        else:
            observed = self.observe_assistant_message_v2(content=new_content, source_id=source_id, session_id=session_id, occurred_at=occurred_at, source_revision=new_revision)
        return {"old_revision": old_revision, "new_revision": new_revision, "observation": observed, "projection": self.rebuild_projection(reason="source_edit")}

    def commit_bound_sidecar(self, *, payload: dict[str, Any], user_message_id: str, assistant_message_id: str, session_id: str = "", assistant_revision: int = 1) -> dict[str, Any]:
        bound = bind_sidecar(payload, user_message_id=user_message_id, assistant_message_id=assistant_message_id, assistant_revision=assistant_revision, session_id=session_id)
        # The final assistant observer normally registered the real content
        # just before this call.  Never replace that source with an empty
        # placeholder; the sidecar is metadata attached to the persisted turn.
        existing_assistant = self.store.get_source_revision("conversation_message", assistant_message_id)
        if not existing_assistant or int(existing_assistant.get("source_revision", 1) or 1) != int(assistant_revision):
            self._register_conversation_source(source_id=assistant_message_id, role="assistant", content="", revision=assistant_revision, session_id=session_id)
        if session_id and not self.store.get_open_episode(session_id):
            # The early user observer is intentionally asynchronous so it can
            # never delay the provider response.  If the final persistence
            # wins the race, recover the already-real user watermark here;
            # this still uses the backend-owned ID and remains inside the SOP
            # lock.  It does not run Flash or change a snapshot.
            user_source = self.store.get_source_revision("conversation_message", user_message_id)
            self.store.append_episode_message(
                session_id=session_id, source_type="conversation_message", source_id=user_message_id,
                source_revision=int((user_source or {}).get("source_revision", 1) or 1), role="user", content=str((user_source or {}).get("content_excerpt") or ""),
                occurred_at=str((user_source or {}).get("occurred_at") or now_iso()), quiet_minutes=int(self.v2_settings().get("quiet_minutes", 30)), trace_id=self.trace_id(),
            )
        if session_id:
            assistant_source = self.store.get_source_revision("conversation_message", assistant_message_id) or {}
            self.store.append_episode_message(
                session_id=session_id, source_type="conversation_message", source_id=assistant_message_id,
                source_revision=assistant_revision, role="assistant",
                content=str(assistant_source.get("content_excerpt") or ""),
                occurred_at=str(assistant_source.get("occurred_at") or now_iso()),
                quiet_minutes=int(self.v2_settings().get("quiet_minutes", 30)), trace_id=self.trace_id(),
            )
        return self.record_self_observation(source_id=assistant_message_id, session_id=session_id, source_revision=assistant_revision, payload=bound["payload"], assistant_source_id=assistant_message_id, user_source_id=user_message_id, immediate=True, source_type="conversation_message", trace_id=self.trace_id())

    # New service names used by routers/hidden integration tests.  The old v1
    # entry points remain above solely for compatibility with existing callers.
    def get_projection(self, *, now: str = "", rebuild: bool = False) -> dict[str, Any]:
        return self.get_v2_projection(now=now, rebuild=rebuild)

    def render_self(self, *, cap: int = 1200) -> str:
        return self._render_v2_self(self.get_v2_projection()["self"], cap=cap)

    def render_resonance(self, *, cap: int = 1200) -> str:
        value = self._render_v2_resonance(self.get_v2_projection()["resonance"])
        return value[:max(100, int(cap))]

    def parse_sidecar(self, content: str) -> dict[str, Any]:
        return parse_affect_sidecar(content).to_dict()

    # v2 overrides for the public names consumed by the existing Context
    # Builder/router scaffold.  Keeping these as methods (rather than a
    # second service object) makes singleton restart and temporary-DB tests
    # deterministic.
    def settings(self) -> dict[str, Any]:  # type: ignore[override]
        return self.v2_settings()

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        return self.update_v2_settings(updates)

    def observe_user_message(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        return self.observe_user_message_v2(**kwargs)

    def submit_self_sidecar(  # type: ignore[override]
        self, *, source_type: str, source_id: str, self_state: dict[str, Any], trace_id: str = "", reason: str = "",
        session_id: str = "", source_revision: int = 1, occurred_at: str = "",
    ) -> dict[str, Any]:
        if not source_id:
            raise ValueError("self sidecar requires a real source_id")
        payload = dict(self_state or {})
        if reason and not payload.get("reason"):
            payload["reason"] = reason
        immediate = source_type == "wander_settlement"
        if immediate:
            self.store.register_source_revision(
                source_type=source_type, source_id=source_id, source_revision=source_revision,
                owner="k", role="wander_settlement", content=reason or payload.get("description", ""),
                metadata={"settled": True}, occurred_at=occurred_at,
            )
        return self.record_self_observation(
            source_id=source_id, session_id=session_id, payload=payload, source_revision=source_revision,
            immediate=immediate, source_type=source_type, trace_id=trace_id,
        )

    def render_emotion_self(self, *, cap: int = 700) -> str:  # type: ignore[override]
        return self.render_self(cap=cap)

    def render_emotion_resonance(self, *, cap: int = 700) -> str:  # type: ignore[override]
        return self.render_resonance(cap=cap)


_service: Optional[AffectService] = None


def get_affect_service() -> AffectService:
    global _service
    if _service is None:
        _service = AffectService()
    return _service


__all__ = ["AffectService", "FLASH_PROMPT_VERSION", "get_affect_service"]
