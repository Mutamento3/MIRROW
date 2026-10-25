"""Small read-only prefetches shared by HTTP and WebSocket main chat."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


async def _bounded(awaitable: Awaitable[Any], default: Any, timeout: float) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except Exception:
        return default


async def prefetch_chat_context_inputs(
    session_id: str,
    *,
    profile_loader: Callable[[str], Awaitable[str]],
    time_hint_loader: Callable[[str, str], Awaitable[Any]],
    current_timestamp: str,
    timeout: float = 5.0,
) -> tuple[str, Any]:
    """Load independent profile and time-awareness inputs concurrently."""

    profile, time_hint = await asyncio.gather(
        _bounded(profile_loader(session_id), "", timeout),
        _bounded(time_hint_loader(session_id, current_timestamp), None, timeout),
    )
    return str(profile or ""), time_hint
