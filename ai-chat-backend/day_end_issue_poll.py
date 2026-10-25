"""Coalesce overlapping UI polls while preserving fresh authority checks."""
import asyncio


class IssuePoll:
    def __init__(self, reader):
        self.reader = reader
        self.task = None

    async def get(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(asyncio.to_thread(self.reader))
            # Retrieve failures even if every HTTP caller has disconnected.
            self.task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None)
        # Client cancellation cannot cancel the one authoritative reconciliation.
        return await asyncio.shield(self.task)


def _read():
    from day_end_issues import listing
    return listing()


poll = IssuePoll(_read)
