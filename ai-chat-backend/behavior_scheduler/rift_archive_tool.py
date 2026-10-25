"""AI-facing fixed tools for the Rift Archive game domain."""

from __future__ import annotations

import json
from typing import Any

from .base_tool import BaseTool, ToolResult, ToolStatus


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class _RiftTool(BaseTool):
    # 2026-08-18：裂隙档案工具不对 AI 主动暴露（人类伙伴决定）——避免 AI 在普通聊天里
    # 主动提出游戏操作、稀释核心工具注意力。工具仍在 tools dict，代码层显式调用
    # （CommandExecutor 按 name 执行）不受影响；未来需要时可单独重新暴露。
    visible_to_pro = False

    async def _run(self, operation, *args, **kwargs) -> ToolResult:
        try:
            result = await operation(*args, **kwargs)
            return ToolResult(status=ToolStatus.SUCCESS, content=_json(result))
        except ValueError as exc:
            return ToolResult(status=ToolStatus.FAILED, content="", error=str(exc))
        except Exception as exc:
            # Remote/network details stay in backend logs.  AI receives a stable
            # capability boundary instead of infrastructure or credential data.
            return ToolResult(
                status=ToolStatus.ERROR,
                content="",
                error=f"裂隙档案服务暂时不可用（{type(exc).__name__}）",
            )


class RiftReadArchiveTool(_RiftTool):
    name = "rift_read_archive"
    description = "读取《裂隙档案》的公开案件目录、开局和非剧透线索；这是虚构游戏资料，不会推进案件。"
    flash_description = "读取裂隙档案公开案件目录与开局"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def get_user_facing_description(self, **kwargs) -> str:
        return "正在翻阅裂隙档案目录..."

    async def execute(self, **kwargs) -> ToolResult:
        from rift_archive import get_rift_archive_service
        return await self._run(get_rift_archive_service().read_archive)


class RiftStartCaseTool(_RiftTool):
    name = "rift_start_case"
    description = (
        "创建一宗新的《裂隙档案》案件并由本机保存凭证。开始前应先读取公开目录，"
        "case_id 与 opening 必须来自目录；创建会改变远端游戏状态。没有人类伙伴当前消息的"
        "明确开局许可时，本工具只登记提案并要求确认，不会访问远端创建案件。"
    )
    flash_description = "提议或在人类伙伴明确同意后创建一宗新的裂隙档案案件"
    parameters_schema = {
        "type": "object",
        "properties": {
            "case_id": {
                "type": "string",
                "enum": ["chapter-one", "chapter-two", "chapter-three", "chapter-four", "chapter-five", "chapter-six"],
            },
            "opening": {"type": "string", "minLength": 1, "maxLength": 48},
        },
        "required": ["case_id", "opening"],
        "additionalProperties": False,
    }

    def get_user_facing_description(self, **kwargs) -> str:
        return "正在拆开这宗案件的火漆..."

    async def execute(self, **kwargs) -> ToolResult:
        from .execution_context import get_request_user_message
        from rift_archive import get_rift_archive_service
        return await self._run(
            get_rift_archive_service().start_case,
            case_id=kwargs.get("case_id", ""), opening=kwargs.get("opening", ""),
            user_message=get_request_user_message(),
        )


class RiftGetStateTool(_RiftTool):
    name = "rift_get_state"
    description = (
        "从远端读取当前已接入案件的最新地点、线索、侵蚀、行动点、日志和可执行行动。"
        "讨论或行动前可调用；不会推进案件。"
    )
    flash_description = "刷新并读取当前裂隙档案局面"
    parameters_schema = {"type": "object", "properties": {}, "required": []}

    def get_user_facing_description(self, **kwargs) -> str:
        return "正在重新核对案件记录..."

    async def execute(self, **kwargs) -> ToolResult:
        from rift_archive import get_rift_archive_service
        return await self._run(get_rift_archive_service().refresh)


class RiftTakeActionTool(_RiftTool):
    name = "rift_take_action"
    description = (
        "在当前已进入的《裂隙档案》游玩窗口中执行最新 availableActions 里的一个 action_id。"
        "执行前会强制刷新局面，行动自动署名 AI；它会消耗时间并改变案件。"
    )
    flash_description = "AI在当前裂隙档案案件中执行一步真实行动"
    pro_guide = "推进《裂隙档案》时，原样引用最新可选行动中的真实 action_id；执行会消耗时间并改变案件。"
    parameters_schema = {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "minLength": 1, "description": "最新 availableActions 中的 actionId"},
        },
        "required": ["action_id"],
        "additionalProperties": False,
    }

    def get_user_facing_description(self, **kwargs) -> str:
        return "AI 正在把这一决定写进案件记录..."

    async def execute(self, **kwargs) -> ToolResult:
        from rift_archive import get_rift_archive_service
        return await self._run(get_rift_archive_service().take_action, kwargs.get("action_id", ""))


def register_rift_archive_tools() -> dict[str, BaseTool]:
    return {
        tool.name: tool for tool in (
            RiftReadArchiveTool(), RiftStartCaseTool(), RiftGetStateTool(), RiftTakeActionTool(),
        )
    }


__all__ = ["register_rift_archive_tools"]
