"""A thin stdio client; the backend remains the sole job/runner writer.

Usage: python -m workshop.mcp_stdio. Set MIRROW_WORKSHOP_API for a local backend.
"""
import json
import os
import sys
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlparse


def main():
    # MCP JSON lines use UTF-8, independently of the Windows console codepage.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    url = os.getenv('MIRROW_WORKSHOP_API', 'http://127.0.0.1:8005/api/workshop/mcp')
    if urlparse(url).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('stdio_proxy_must_use_loopback')
    opener = build_opener(ProxyHandler({}))
    for line in sys.stdin:
        try:
            message = json.loads(line)
            req = Request(url, data=json.dumps(message).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            with opener.open(req, timeout=45) as response:
                data = response.read()
            if data:
                print(data.decode(), flush=True)
        except Exception:
            print(json.dumps({'jsonrpc': '2.0', 'id': (message or {}).get('id') if 'message' in locals() else None, 'error': {'code': -32000, 'message': 'workshop_backend_unavailable'}}), flush=True)


if __name__ == '__main__':
    main()
