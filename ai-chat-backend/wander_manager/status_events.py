"""Immutable status-event facts; no sensor, model, or persistence side effects."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4


def make_status_event(*, previous: str, current: str, previous_label: str,
                      current_label: str, previous_detail: str, detail: str,
                      source: str, occurred_at: str | None = None) -> dict:
    recorded_at = datetime.now().isoformat()
    occurred_at = occurred_at or recorded_at
    kind = "transition" if previous != current else "detail_change"
    origin = {"explicit": "人类伙伴手动设置", "auto": "设备状态引擎推断",
              "wakeup": "系统唤醒规则", "topic_end": "系统话题结束规则"}.get(source, "来源未标明")
    if kind == "transition":
        before = previous_label + (f"（{previous_detail}）" if previous_detail else "")
        after = current_label + (f"（{detail}）" if detail else "")
        fact = f"人类伙伴的状态由{before}变为{after}。"
    else:
        before = previous_detail or "未填写"
        after = detail or "已清空"
        fact = f"人类伙伴的{current_label}状态描述由「{before}」改为「{after}」；状态类别未变。"
    return {
        "id": uuid4().hex, "time": occurred_at, "recorded_at": recorded_at,
        "from": previous, "to": current, "kind": kind, "source": source,
        "previous_custom_text": previous_detail, "custom_text": detail,
        "context_event_version": 1,
        # Frozen at acquisition, not regenerated from mutable labels/snapshots.
        "context_text": f"发生时间：{occurred_at}；记录时间：{recorded_at}；来源：{origin}。\n{fact}",
    }
