"""Retry durable local event-card writes independently of autonomous activity."""

import asyncio
import logging


async def recover_activity_events(share_adapter):
    while True:
        try:
            await share_adapter.retry_activity_events()
        except Exception:
            logging.getLogger(__name__).exception("漫想事件卡恢复暂未完成，保留持久待写记录")
        await asyncio.sleep(60)
