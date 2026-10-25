"""Network and browser-origin admission for the whole local HTTP/WS app."""
import os
from starlette.responses import JSONResponse


def allowed_browser_origins():
    origins = {'http://localhost', 'https://localhost', 'capacitor://localhost'}
    for port in (3000, 8080, 4173, 5173, 5174, 8000, 8005):
        for host in ('localhost', '127.0.0.1'):
            for scheme in ('http', 'https'):
                origins.add(f'{scheme}://{host}:{port}')
    # Explicit origins only: wildcard/null never grants access to the body.
    origins.update(value.strip() for value in os.getenv('ALLOWED_ORIGINS', '').split(',')
                   if value.strip() not in {'', '*', 'null'})
    return frozenset(origins)


class LocalAccessBoundary:
    """Origin is an additional browser check, never a replacement for peer admission.

    Non-browser clients without Origin retain the registered-device path.
    Lifespan messages pass through without touching devices or runtime state.
    """
    def __init__(self, app, network_policy, allowed_origins):
        self.app = app
        self.network_policy = network_policy
        self.allowed_origins = frozenset(allowed_origins) - {'', '*', 'null'}

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        peer = scope.get('client')
        origins = [value.decode('latin-1') for key, value in scope.get('headers', ())
                   if key.lower() == b'origin']
        origin_ok = not origins or (len(origins) == 1 and origins[0] in self.allowed_origins)
        if not self.network_policy.allows(peer[0] if peer else None) or not origin_ok:
            if kind == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            else:
                await JSONResponse({'detail': 'Access denied'}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
