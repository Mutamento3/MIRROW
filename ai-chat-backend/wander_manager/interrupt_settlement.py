"""Bound foreground waiting without cancelling the durable activity settlement."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class InterruptSettlement:
    """Own one background settlement per activity, independently of a chat turn."""

    def __init__(self, *, wait_seconds: float = 2.0):
        self.wait_seconds = wait_seconds
        self._tasks: dict[str, asyncio.Task] = {}

    async def wait(self, runner: Any, snapshot: dict) -> dict:
        activity_id = str(snapshot.get("activity_id") or "")
        if not activity_id:
            return snapshot
        task = self._tasks.get(activity_id)
        if task is None:
            task = asyncio.create_task(
                self._settle(runner, activity_id),
                name=f"wander-interrupt-settlement:{activity_id}",
            )
            self._tasks[activity_id] = task

        # Bind updates to this captured snapshot, never to the manager's later
        # current snapshot. A returning user can interrupt another activity.
        def completed(done: asyncio.Task) -> None:
            if not done.cancelled():
                activity = done.result()  # _settle handles failures, including after timeout.
                if activity:
                    if activity.get("summary"):
                        snapshot["summary"] = activity["summary"]
                    snapshot["settlement_reason"] = activity.get("settlement_reason") or ""
            if self._tasks.get(activity_id) is done:
                self._tasks.pop(activity_id, None)

        task.add_done_callback(completed)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.wait_seconds)
        except asyncio.TimeoutError:
            logger.info("漫想中断结算继续在后台运行，主聊天继续: activity_id=%s", activity_id)
        return snapshot

    async def _settle(self, runner: Any, activity_id: str) -> dict | None:
        try:
            # The runtime's existing lock and durable state remain authoritative.
            return await runner.settle_interrupt(activity_id)
        except Exception:
            logger.exception("v3 用户中断结算失败: activity_id=%s", activity_id)
            return None

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
