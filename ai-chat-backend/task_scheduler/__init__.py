# 定时任务管理器
#
# 管理定时提醒和消息任务，支持：
# - AI 通过行为调度器工具创建任务
# - 手动 CRUD 管理
# - cron 表达式 / 一次性日期时间触发
# - Windows 桌面通知 / 聊天消息推送
# - APScheduler 每分钟轮询到期任务

from .manager import TaskManager, get_global_task_manager, init_global_task_manager
from .models import ScheduledTask, TaskCreateRequest, TaskUpdateRequest
from .router import router

__all__ = [
    "TaskManager",
    "get_global_task_manager",
    "init_global_task_manager",
    "ScheduledTask",
    "TaskCreateRequest",
    "TaskUpdateRequest",
    "router",
]
