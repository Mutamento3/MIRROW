"""Private-chat admission while a human-invited game is running.

Only chat is gated: reminders and the rest of AI's body continue to work.
The durable invited run is restored into this process-local gate on startup.
"""

import asyncio
import uuid

_reserved = False
_chats: set[str] = set()
BUSY_MESSAGE = "AI 正在玩你邀请的游戏；可以先写草稿，结束游玩后再发送。"


def status() -> dict:
    return {"busy": _reserved, "message": BUSY_MESSAGE if _reserved else ""}


def enter_chat() -> str:
    if _reserved:
        raise RuntimeError(BUSY_MESSAGE)
    lease = uuid.uuid4().hex
    _chats.add(lease)
    return lease


def leave_chat(lease: str | None) -> None:
    if lease:
        _chats.discard(lease)


def reserve() -> None:
    global _reserved
    if _reserved:
        raise RuntimeError(BUSY_MESSAGE)
    _reserved = True


async def drain() -> None:
    while _chats:
        await asyncio.sleep(0.05)


def release() -> None:
    global _reserved
    _reserved = False
