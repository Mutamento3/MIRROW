"""Optional commit gate for attachments of an autonomous message."""


async def after_delivery(gate, generate):
    # Ordinary chat has no gate and retains its existing execution lifecycle.
    if gate is not None:
        await gate.wait()
        if not gate.accepted:
            return
    await generate()


class AttachmentDeliveryGate:
    def __init__(self):
        import asyncio
        self._ready = asyncio.Event()
        self.accepted = False

    async def wait(self):
        await self._ready.wait()

    def finish(self, accepted):
        if self._ready.is_set():
            return
        self.accepted = bool(accepted)
        self._ready.set()
