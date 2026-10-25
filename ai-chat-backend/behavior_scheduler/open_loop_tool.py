"""AI 主动维护当前未闭合关注的轻量工具。"""

from typing import Any, Dict

from .base_tool import BaseTool, ToolResult, ToolStatus


class KeepInMindTool(BaseTool):
    name = "keep_in_mind"
    description = (
        "AI当前意识中的未闭合关注存储。内容是AI主动选择继续保留的短句，"
        "可与提醒、待办或愿望同时存在。action='hold'保留 / 'revise'修改 / "
        "'reinforce'继续保留 / 'snooze'暂放到指定时间 / 'release'结束关注。"
    )
    flash_description = "开放线索——AI保留、修改、暂放或结束当前挂念的短句"

    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["hold", "revise", "reinforce", "snooze", "release"],
                "description": "操作：hold保留新线索，revise修改，reinforce继续保留，snooze暂放，release结束关注",
            },
            "content": {
                "type": "string",
                "description": "AI想继续保留的短句；hold/revise时使用",
            },
            "loop_id": {
                "type": "string",
                "description": "已有挂念的完整原句或线索ID；revise/reinforce/snooze/release时使用",
            },
            "wake_at": {
                "type": "string",
                "description": "再次进入当前意识的ISO日期时间；snooze时使用",
            },
        },
        "required": ["action"],
    }

    @property
    def input_schema(self) -> Dict[str, Any]:
        return self.parameters_schema

    def get_user_facing_description(self, **kwargs) -> str:
        return {
            "hold": "正在留住这个念头...",
            "revise": "正在整理这个念头...",
            "reinforce": "正在继续留住这个念头...",
            "snooze": "正在暂时放下这个念头...",
            "release": "正在放下这个念头...",
        }.get(kwargs.get("action", ""), "正在整理心里的挂念...")

    async def execute(self, **kwargs) -> ToolResult:
        from open_loops import get_open_loop_store

        action = kwargs.get("action", "")
        store = get_open_loop_store()
        try:
            if action != "hold":
                kwargs["loop_id"] = store.resolve_reference(kwargs.get("loop_id", ""))
            if action == "hold":
                item = store.hold(kwargs.get("content", ""))
                text = f"已保留开放线索 {item['id']}: {item['content']}"
            elif action == "revise":
                item = store.revise(kwargs.get("loop_id", ""), kwargs.get("content", ""))
                if not item:
                    raise ValueError("未找到要修改的开放线索")
                text = f"已修改开放线索 {item['id']}: {item['content']}"
            elif action == "snooze":
                item = store.snooze(kwargs.get("loop_id", ""), kwargs.get("wake_at", ""))
                if not item:
                    raise ValueError("未找到可暂放的开放线索")
                text = f"开放线索 {item['id']} 已暂放至 {item['wake_at']}"
            elif action == "reinforce":
                item = store.reinforce(kwargs.get("loop_id", ""))
                if not item:
                    raise ValueError("未找到可继续保留的开放线索")
                text = f"已继续保留开放线索 {item['id']}: {item['content']}"
            elif action == "release":
                item = store.release(kwargs.get("loop_id", ""))
                if not item:
                    raise ValueError("未找到可结束的开放线索")
                text = f"已结束开放线索 {item['id']}: {item['content']}"
            else:
                raise ValueError(f"未知操作: {action}")
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content=text,
                delivery="ui_only",
            )
        except Exception as exc:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="",
                error=str(exc),
                delivery="ui_only",
            )
