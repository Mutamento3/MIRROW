"""Exact V2 source pagination, with read-only legacy scene compatibility."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .base_tool import BaseTool, ToolResult, ToolStatus
from message_roles import EVENT_ROLE, NOTIFICATION_ROLE

logger = logging.getLogger(__name__)


def _bucket_id_candidates(bucket_id: str) -> list[str]:
    """Accept both the OB client display id and the on-disk bucket id."""
    value = str(bucket_id or "").strip()
    if value.startswith("ob_rev_"):
        return [value, value[len("ob_rev_"):]]
    return [value]


def _iso_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未知时间"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S") if parsed.tzinfo else parsed.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text


def _message_content(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


class SourceTraceTool(BaseTool):
    """Trace a current memory ID or an explicitly requested old scene."""

    name = "fetch_source_details"
    visible_to_pro = True
    description = (
        "原文追溯：memory_id 查看 Memory V2 事件／事件链的精确来源分页；bucket_id 兼容旧档案，scene_id 查看历史场景。"
        "返回来源时间、摘录和明确的分页／截取状态。"
    )
    flash_description = "查记忆对应的历史原文和前因后果"
    pro_guide = ("需要核对记忆原话、原因或过程时追溯来源；原样引用已有检索结果中的 memory_id、旧 bucket_id 或 scene_id。"
                 "Memory V2 来源可分页继续读，历史场景可按已有 scene_id 追溯；历史材料仍按发生时间理解。"
                 "没有来源标识时先检索，不自造标识。")
    parameters_schema = {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory V2 检索返回的事件或事件链 ID"},
            "source_offset": {"type": "integer", "minimum": 0, "description": "V2 原文分页起点，默认 0，每页至多 5 条"},
            "bucket_id": {
                "type": "string",
                "description": "旧档案 bucket_id（与 memory_id、scene_id 三选一）",
            },
            "scene_id": {
                "type": "string",
                "description": "已知的 scene_id（与 memory_id、bucket_id 三选一）",
            },
        },
        "required": [],
    }

    async def _load_bucket(self, bucket_id: str) -> Tuple[Optional[dict], str]:
        from memory_v2.legacy_archive import load_legacy_bucket
        for candidate in _bucket_id_candidates(bucket_id):
            if not candidate:
                continue
            bucket = await load_legacy_bucket(candidate)
            if bucket:
                return bucket, candidate
        return None, str(bucket_id or "")

    async def _resolve_scene(self, bucket_id: str = "", scene_id: str = "") -> Tuple[Optional[dict], Optional[dict], str]:
        from event_chronicle import get_global_chronicle
        from scene_manager.scene import get_scene_by_id, get_scene_for_ts

        chronicle = get_global_chronicle()
        bucket = None
        resolved_bucket_id = ""
        if bucket_id:
            bucket, resolved_bucket_id = await self._load_bucket(bucket_id)
            if not bucket:
                return None, None, resolved_bucket_id

        meta = (bucket or {}).get("metadata") or {}
        linked_scene_id = str(meta.get("source_scene_id") or (bucket or {}).get("source_scene_id") or "")
        linked_source_ts = str(meta.get("source_ts") or (bucket or {}).get("source_ts") or "")
        requested_scene_id = str(scene_id or linked_scene_id).strip()
        scene = get_scene_by_id(requested_scene_id, chronicle=chronicle) if requested_scene_id else None
        if scene is None and linked_source_ts:
            scene = get_scene_for_ts(linked_source_ts, chronicle=chronicle)
        return scene, bucket, resolved_bucket_id

    @staticmethod
    def _format_source_block(scene: dict, messages: list[dict], anchor_types: list[str]) -> str:
        start = _iso_label(scene.get("start_ts"))
        end = _iso_label(scene.get("end_ts"))
        session_id = str(scene.get("session_id") or "（未记录会话）")
        date = str(scene.get("active_date") or str(scene.get("start_ts") or "")[:10] or "未知日期")
        anchors = "、".join(anchor_types) if anchor_types else "无额外锚点"

        lines = [
            "【历史原文引用开始】",
            "以下内容只来自过去保存的同一 scene，属于历史原文核对材料，不是今日或昨日对话。",
            f"时间：{start} ～ {end}（活跃日 {date}）",
            f"会话：{session_id}",
            f"scene_id：{scene.get('id', '')}",
            f"锚点类型：{anchors}",
            "原文（完整 scene）：",
        ]
        if not messages:
            lines.append("（该 scene 没有可读取的消息）")
        else:
            for index, message in enumerate(messages, 1):
                role = str(message.get("role") or "unknown")
                if role in {NOTIFICATION_ROLE, EVENT_ROLE}:
                    continue
                timestamp = _iso_label(message.get("timestamp"))
                content = _message_content(message)
                if role == "divider":
                    content = content or "（对话分隔标记）"
                lines.append(f"[{index}] {timestamp} | role={role}\n{content}")
        lines.append("【历史原文引用结束】")
        return "\n".join(lines)

    async def execute(self, **kwargs) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        bucket_id = str(kwargs.get("bucket_id") or "").strip()
        scene_id = str(kwargs.get("scene_id") or "").strip()
        if sum(bool(value) for value in (memory_id, bucket_id, scene_id)) != 1:
            return ToolResult(
                status=ToolStatus.ERROR,
                content="",
                error="memory_id、bucket_id 和 scene_id 必须三选一",
            )

        try:
            current_id = memory_id or bucket_id
            if (memory_id and not memory_id.startswith("legacy:")) or current_id.startswith(("event:", "thread:", "group:")):
                import asyncio
                from memory_v2.library import memory_detail
                offset = kwargs.get("source_offset", 0)
                if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                    return ToolResult(status=ToolStatus.ERROR, content="", error="source_offset 应为非负整数")
                sources = await asyncio.to_thread(memory_detail, current_id, offset=offset, limit=5, sources=True)
                lines = ["【记忆来源原文】", f"来源总数：{sources['total']}；本页起点：{offset}"]
                for message in sources["messages"]:
                    suffix = "（摘录已截取）" if message["content_truncated"] else ""
                    lines.append(f"{_iso_label(message['timestamp'])} | {message['role']}\n{message['content']}{suffix}")
                next_offset = offset + sources["limit"]
                more = next_offset < sources["total"]
                lines.append(f"仍有后续来源：{'是' if more else '否'}")
                return ToolResult(status=ToolStatus.SUCCESS, content="\n".join(lines),
                    extra_data={"memory_id": sources["id"], "source_total": sources["total"],
                                "source_offset": offset, "next_source_offset": next_offset if more else None,
                                "truncated": sources["truncated"]})
            if memory_id:
                bucket_id = memory_id.removeprefix("legacy:")
            scene, bucket, resolved_bucket_id = await self._resolve_scene(bucket_id, scene_id)
            if bucket_id and bucket is None:
                return ToolResult(status=ToolStatus.ERROR, content="", error=f"记忆桶不存在: {bucket_id}")
            if scene is None:
                if scene_id:
                    return ToolResult(status=ToolStatus.ERROR, content="", error=f"scene 不存在: {scene_id}")
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    content=(
                        "该旧资料没有可定位的场景链接，本次无法核对原文。"
                    ),
                    extra_data={"pending": True, "source_state": "untraceable", "bucket_id": resolved_bucket_id, "scene_id": scene_id},
                )

            from event_chronicle import get_global_chronicle
            from scene_manager.anchor import anchor_types_for
            from scene_manager.scene import get_scene_messages

            chronicle = get_global_chronicle()
            messages = get_scene_messages(scene, chronicle=chronicle)
            anchor_types = anchor_types_for(
                scene,
                chronicle=chronicle,
                memory_buckets=[bucket] if bucket else None,
                message_ids=[str(m.get("message_id") or m.get("id") or "") for m in messages],
            )
            content = self._format_source_block(scene, messages, anchor_types)
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content=content,
                extra_data={
                    "scene_id": scene.get("id", ""),
                    "bucket_id": resolved_bucket_id,
                    "anchor_types": anchor_types,
                    "message_count": len(messages),
                },
            )
        except Exception as exc:
            logger.warning("fetch_source_details failed: %s", type(exc).__name__)
            return ToolResult(status=ToolStatus.ERROR, content="", error="原文追溯暂不可核验，请刷新检索结果后重试")
