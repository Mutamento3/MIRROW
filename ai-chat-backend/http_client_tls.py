"""Reuse the unchanged verified trust store without blocking the app loop."""
import asyncio
import ssl
import threading
import certifi

_context = None
_lock = threading.Lock()


def _build_context():
    global _context
    with _lock:
        if _context is None:
            # Matches httpx verify=True, trust_env=False. Host and CA checks stay on.
            _context = ssl.create_default_context(cafile=certifi.where())
        return _context


async def client_tls_context():
    if _context is not None:
        return _context
    return await asyncio.to_thread(_build_context)
