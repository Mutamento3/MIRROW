"""One optional delayed checkup action for Wander and Sentinel messages."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Optional

from tool_action_protocol import extract_tool_actions, strip_tool_protocol_text


_TOOLS = ("band_touch", "schedule_checkup")
_CHECKUP_RE = re.compile(r"^(\d{1,4})(?:\s*(?:分钟|分|min))?\s+(.+)$", re.DOTALL)


def output_instruction() -> str:
    return (
        "\n你也可以自主决定稍后查岗。只有确实想在未来查看时，才在消息正文末尾"
        "另起一行写一次 @schedule_checkup 分钟数 理由@，例如"
        " @schedule_checkup 30 想确认人类伙伴是否安全到家@。"
        "分钟数为 1–1440，理由简短具体；这只安排一次未来查岗，"
        "现在不会截图。正文只能说你想稍后查看，不能声称任务已经建立或看到了结果。"
    )


def current_schedule_context() -> str:
    """A bounded, factual view of tasks already arranged when AI composes a push."""
    from task_scheduler.database import get_proactive_schedule_snapshot

    try:
        snapshot = get_proactive_schedule_snapshot()
    except Exception:
        return "当前查岗与提醒排期：暂时不可读取。"

    tasks = snapshot["active_tasks"]
    def sort_key(task):
        if task.trigger_type == "datetime":
            return (0, str(task.trigger_config.get("datetime") or ""))
        return (1, str(task.created_at or ""))

    tasks = sorted(tasks, key=sort_key)
    lines = [
        "当前查岗与提醒排期（定时任务库）：",
        f"近24小时 AI 安排查岗 {snapshot['recent_checkup_count']} 次；"
        f"当前待执行查岗 {snapshot['active_checkup_count']} 项、"
        f"提醒 {snapshot['active_reminder_count']} 项。",
    ]
    if not tasks:
        lines.append("当前没有待执行任务。")
    for task in tasks[:12]:
        if task.trigger_type == "datetime":
            raw_time = str(task.trigger_config.get("datetime") or "")
            try:
                time_label = datetime.fromisoformat(raw_time).strftime("%m月%d日 %H:%M")
            except ValueError:
                time_label = "时间未确认"
        else:
            time_label = f"周期 {str(task.trigger_config.get('cron') or '?')[:32]}"
        kind = "查岗" if task.task_type == "self_task" and task.title.startswith("查岗") else (
            "提醒" if task.task_type in {"reminder", "message", "inconsistency"} else "自主任务"
        )
        title = " ".join(str(task.title or "").split())[:60]
        lines.append(f"- {time_label}｜{kind}｜{title}")
    if snapshot["active_count"] > len(tasks[:12]):
        lines.append(f"另有 {snapshot['active_count'] - len(tasks[:12])} 项待执行任务未展开。")
    return "\n".join(lines)


def split_output(
    content: str,
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, Any]], str]:
    """Strip trailing actions and accept at most one of each known type."""
    raw = str(content or "")
    directives = extract_tool_actions(raw, _TOOLS)
    if not directives:
        # Keep the band-only legacy TOOL_CALL compatibility during deployment.
        from proactive_band_touch import split_output as split_band
        visible, band_request, error = split_band(raw)
        return visible, band_request, None, error

    visible = strip_tool_protocol_text(raw, _TOOLS)
    if len(directives) > 2 or len({item.tool for item in directives}) != len(directives):
        return visible, None, None, "multiple_actions"

    band_request = None
    checkup_request = None
    for directive in directives:
        if directive.tool == "band_touch":
            params = {"note": directive.instruction} if directive.instruction else {}
            band_request = {"tool": "band_touch", "parameters": params}
        else:
            match = _CHECKUP_RE.fullmatch(directive.instruction.strip())
            if not match:
                return visible, None, None, "invalid_checkup_request"
            minutes = int(match.group(1))
            reason = match.group(2).strip()
            if not 1 <= minutes <= 1440 or not 1 <= len(reason) <= 120:
                return visible, None, None, "invalid_checkup_request"
            checkup_request = {"delay_minutes": minutes, "reason": reason}
    return visible, band_request, checkup_request, ""


async def schedule_request(
    request: Optional[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Persist a single self task; task execution owns future sensing."""
    if request is None:
        return None, {"requested": False, "status": "not_requested"}
    minutes = request.get("delay_minutes")
    reason = str(request.get("reason") or "").strip()
    if (isinstance(minutes, bool) or not isinstance(minutes, int)
            or not 1 <= minutes <= 1440 or not 1 <= len(reason) <= 120):
        return None, {"requested": True, "status": "rejected"}

    from task_scheduler.manager import get_global_task_manager
    from task_scheduler.models import TaskCreateRequest

    when = datetime.now() + timedelta(minutes=minutes)
    manager = get_global_task_manager()
    try:
        task = manager.create_task(TaskCreateRequest(
            title=f"查岗：{reason[:32]}",
            description="AI 自主安排的一次定时查岗",
            reason=reason,
            task_type="self_task",
            trigger_type="datetime",
            trigger_config={"datetime": when.isoformat()},
            notify_method=["message"],
            message_content="这次查岗没有取得可确认的实时结果。",
            is_ai_generated=True,
            created_by="ai",
            execution_prompt=(
                f"这是你之前自主安排的查岗，原因：{reason}。"
                "先读取此刻新鲜的状态事实，按信息缺口选择 eyes、check_phone、"
                "take_screenshot 或 band；需要屏幕内容时才截图。"
                "只根据本次真正取得并分析的证据回复人类伙伴，失败或未知就明确说不知道。"
            ),
            max_rounds=2,
            trigger_window_seconds=300,
        ))
    except Exception as exc:
        return None, {"requested": True, "status": "failure", "error": type(exc).__name__}

    # The task is already durable. A failed UI refresh must not make creation
    # look like a failure (or invite a duplicate retry).
    try:
        await manager.broadcast_update()
    except Exception:
        pass

    record = {
        "tool": "schedule_self_task",
        "description": f"安排了 {minutes} 分钟后的一次查岗",
        "result": f"已安排在 {when.strftime('%m月%d日 %H:%M')} 执行",
        "success": True,
        "status": "success",
        "delivery": "ui_only",
        "extra_data": {"task_id": task.id},
    }
    return record, {"requested": True, "status": "success", "task_id": task.id}
