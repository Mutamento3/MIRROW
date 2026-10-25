"""Combine process memory and raw archive reads without reranking their evidence."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from .base_tool import ToolResult, ToolStatus


async def combine_searches(memory: Awaitable[ToolResult], archive: Awaitable[ToolResult]) -> ToolResult:
    # Each lane retains its own read contract, budget and provenance. A raw
    # archive's similarity score is not a score for the event's current state.
    outcomes = await asyncio.gather(memory, archive, return_exceptions=True)
    blocks = []
    metadata = {}
    successes = 0
    for name, title, outcome in zip(
        ("memory", "archive"), ("记忆过程与来源", "原始生活档案"), outcomes,
    ):
        if isinstance(outcome, BaseException) or outcome.status != ToolStatus.SUCCESS:
            blocks.append(f"[{title}]\n这一分区本次未完成读取。")
            if name == "memory":
                metadata["memory_v2_search"] = {"status": "error"}
            else:
                metadata["archive_search_status"] = "error"
            continue
        successes += 1
        if outcome.content:
            blocks.append(f"[{title}]\n{outcome.content}")
        metadata.update(outcome.extra_data or {})
        if name == "archive":
            metadata["archive_search_status"] = "success"
    return ToolResult(
        status=ToolStatus.SUCCESS if successes else ToolStatus.ERROR,
        content="\n\n".join(blocks),
        extra_data=metadata,
        error="书柜各分区本次均未完成读取" if not successes else None,
    )
