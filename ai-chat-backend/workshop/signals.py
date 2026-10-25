"""Dispatcher wakeups also notify independent collaboration observers."""
import asyncio


class WakeSignal(asyncio.Event):
    def __init__(self):
        super().__init__()
        self.generation = 0
        self.listeners = set()

    def set(self):
        self.generation += 1
        for waiter in tuple(self.listeners):
            if not waiter.done():
                waiter.set_result(self.generation)
        super().set()

    async def changed(self, generation):
        if generation != self.generation:
            return
        waiter = asyncio.get_running_loop().create_future()
        self.listeners.add(waiter)
        try:
            await waiter
        finally:
            self.listeners.discard(waiter)
