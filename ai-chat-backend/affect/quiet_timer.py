"""Affect quiet-checkpoint 的安全后台定时器。

定时器只消费 affect.db 中已达到 quiet watermark 或已由下一轮真实 user 消息
封存的 bounded episode；它不切分每日聊天周期，不插 divider，也不写记忆库。没有 Flash caller 或
disabled 模式时不发起外部评价。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .service import AffectService, get_affect_service

logger = logging.getLogger(__name__)
_task: Optional[asyncio.Task] = None


async def _loop(service: AffectService, interval_seconds: int) -> None:
    while True:
        try:
            settings = service.settings()
            caller = service.get_flash_caller()
            if caller and settings.get("engine_mode", settings.get("context_mode", "shadow")) != "disabled":
                # The store exposes only a completed-turn active checkpoint
                # or a completed-turn box past its quiet deadline.
                try:
                    await service.settle_due_episodes(flash_call=caller)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("affect v2 quiet settlement failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("affect quiet checkpoint tick failed", exc_info=True)
        await asyncio.sleep(max(5, int(interval_seconds)))


def start_quiet_timer(*, service: Optional[AffectService] = None, interval_seconds: int = 30) -> Optional[asyncio.Task]:
    global _task
    if _task is not None and not _task.done():
        return _task
    try:
        _task = asyncio.create_task(_loop(service or get_affect_service(), interval_seconds), name="affect-quiet-checkpoint")
    except RuntimeError:
        _task = None
    return _task


async def stop_quiet_timer() -> None:
    global _task
    task = _task
    _task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def timer_status() -> dict:
    task = _task
    return {"running": bool(task and not task.done()), "task_name": task.get_name() if task else ""}


__all__ = ["start_quiet_timer", "stop_quiet_timer", "timer_status"]
