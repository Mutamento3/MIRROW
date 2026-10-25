"""Origins used by MIRROW's registered personal devices and local frontends.

This is one check in local_ui, after the network/device and explicit management
header checks. It is never sufficient to grant management access by itself.
"""
from urllib.parse import urlsplit
import ipaddress

_APP_ORIGINS = frozenset({'http://localhost', 'https://localhost', 'capacitor://localhost'})
_FRONTEND_PORTS = frozenset({5173, 5174})


def management_origin_allowed(origin: str, request_url: str) -> bool:
    if origin in _APP_ORIGINS:
        return True
    try:
        source, target = urlsplit(origin), urlsplit(request_url)
        if (source.scheme not in {'http', 'https'} or not source.hostname
                or source.username is not None or source.password is not None
                or source.path or source.query or source.fragment):
            return False
        source_port = source.port or (443 if source.scheme == 'https' else 80)
        target_port = target.port or (443 if target.scheme == 'https' else 80)
        if (source.scheme, source.hostname, source_port) == (target.scheme, target.hostname, target_port):
            return True
        if source.scheme != target.scheme or source_port not in _FRONTEND_PORTS:
            return False
        if source.hostname == target.hostname:
            return True
        # Desktop uses localhost and 127.0.0.1 interchangeably. Remote names do
        # not receive this equivalence and are not resolved through DNS here.
        def loopback(host):
            if host == 'localhost':
                return True
            try:
                return ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False
        return loopback(source.hostname) and loopback(target.hostname)
    except ValueError:
        return False
