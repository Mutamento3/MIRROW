"""
定时任务管理器 - 数据模型
"""
from pydantic import BaseModel
from typing import Optional, List


class ScheduledTask(BaseModel):
    """定时任务"""
    id: str
    title: str
    description: str = ""
    reason: str = ""                   # 提醒原因：为什么设置这个提醒
    ai_context: str = ""               # AI生成消息的上下文提示（触发时喂给LLM）
    task_type: str = "reminder"        # "reminder" | "inconsistency" | "message" | "self_task"
    trigger_type: str                  # "cron" | "datetime"
    trigger_config: dict               # {"cron": "0 9 * * *"} or {"datetime": "2026-04-26T14:30:00"}
    notify_method: List[str] = ["popup"]  # "popup", "message"
    alarm_enabled: bool = False           # 到点同步触发手机原生闹钟；按掉后进入 5 分钟回应期
    message_content: str = ""          # 兜底消息（LLM生成失败时的备用）
    is_active: bool = True
    is_ai_generated: bool = False
    created_by: str = "user"           # "user" | "ai"
    # self_task 专用字段
    tool_name: str = ""                # AI 计划调用的工具名
    tool_params: dict = {}             # 工具参数 JSON
    execution_prompt: str = ""         # 醒来后执行的指令
    max_rounds: int = 2                # 最大 tool calling 轮次
    trigger_window_seconds: int = 300  # 保鲜窗口（秒），超时静默跳过
    last_triggered_at: Optional[str] = None
    created_at: str
    updated_at: str


class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    title: str
    description: str = ""
    reason: str = ""
    ai_context: str = ""
    task_type: str = "reminder"
    trigger_type: str
    trigger_config: dict
    notify_method: List[str] = ["popup"]
    alarm_enabled: bool = False
    message_content: str = ""
    is_ai_generated: bool = False
    created_by: str = "user"
    # self_task 专用字段
    tool_name: str = ""
    tool_params: dict = {}
    execution_prompt: str = ""
    max_rounds: int = 2
    trigger_window_seconds: int = 300


class TaskUpdateRequest(BaseModel):
    """更新任务请求"""
    title: Optional[str] = None
    description: Optional[str] = None
    reason: Optional[str] = None
    ai_context: Optional[str] = None
    task_type: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[dict] = None
    notify_method: Optional[List[str]] = None
    alarm_enabled: Optional[bool] = None
    message_content: Optional[str] = None
    is_active: Optional[bool] = None
    created_by: Optional[str] = None


class PendingReminder(BaseModel):
    """待复查提醒/查岗事件。

    ``workflow_state`` 只描述查岗闹钟链路；普通日程提醒仍沿用
    ``recheck_stage`` + ``resolved`` 的旧语义。
    """
    id: str = ""
    task_id: str = ""
    task_title: str = ""
    reason: str = ""
    ai_context: str = ""
    session_id: str = ""
    original_message: str = ""
    triggered_at: str = ""
    recheck_stage: int = 0
    resolved: int = 0
    reminder_type: str = "task"  # "task" = 日程提醒, "inconsistency" = 查岗
    workflow_state: str = "pending"  # pending/alarm_pending/alarm_scheduled/alarm_fired/grace_wait/finalizing/resolved
    alarm_id: str = ""
    alarm_scheduled_at: str = ""
    alarm_trigger_at: str = ""
    alarm_dismissed_at: str = ""
    grace_due_at: str = ""
    resolved_at: str = ""
    last_error: str = ""
    alarm_cancel_pending: int = 0
