"""Private API for per-AI-message Memory V2 recall diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from context_inspection import require_private_inspection
from memory_v2.trace_store import get_default_trace_store


router = APIRouter(prefix="/api/memory-v2/traces", tags=["memory-v2-diagnostics"])


class MemoryRecallFeedbackRequest(BaseModel):
    label: str
    note: str = Field(default="", max_length=1_000)


@router.get("/by-assistant/{assistant_message_id}")
async def get_memory_recall_trace(assistant_message_id: str, request: Request):
    require_private_inspection(request)
    trace = get_default_trace_store().get_by_assistant_message(assistant_message_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="这条 AI 消息没有可用的记忆召回记录")
    return {"trace": trace}


@router.post("/{trace_id}/feedback")
async def submit_memory_recall_feedback(
    trace_id: str,
    body: MemoryRecallFeedbackRequest,
    request: Request,
):
    require_private_inspection(request)
    try:
        feedback = get_default_trace_store().append_feedback(
            trace_id, body.label, body.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if feedback is None:
        raise HTTPException(status_code=404, detail="记忆召回记录不存在")
    return {"ok": True, "feedback": feedback}


__all__ = ["router"]
