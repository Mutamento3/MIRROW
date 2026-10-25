"""Small dependency-free MCP JSON-RPC transport for local commerce agents.

The domain packages use this module only for transport.  It supports the
subset MIRROW needs (initialize, ping, tools/list and tools/call) over stdio
and a loopback-only HTTP endpoint.  No public bind and no arbitrary method or
tool dispatch are exposed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Awaitable, Callable, Mapping


Handler = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class McpToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": dict(self.input_schema)}


def text_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {"result": value}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "structuredContent": payload}
    if is_error:
        result["isError"] = True
    return result


class McpJsonRpcDispatcher:
    """Strict dispatcher shared by each independently persisted domain MCP."""

    def __init__(self, *, name: str, version: str, tools: tuple[McpToolSpec, ...], handlers: Mapping[str, Handler]):
        self.name = str(name)
        self.version = str(version)
        self.tools = tuple(tools)
        self.handlers = dict(handlers)
        allowed = {tool.name for tool in self.tools}
        if set(self.handlers) != allowed:
            raise ValueError("mcp_tool_spec_handler_mismatch")

    async def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, Mapping):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid_request"}}
        request_id = request.get("id")
        method = str(request.get("method") or "")
        if not method.startswith(("initialize", "notifications/", "tools/", "ping", "shutdown")):
            return self._error(request_id, -32601, "method_not_found")
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            return self._ok(request_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method == "ping":
            return self._ok(request_id, {"status": "alive", "service": self.name})
        if method == "shutdown":
            return self._ok(request_id, {})
        if method == "tools/list":
            return self._ok(request_id, {"tools": [tool.to_dict() for tool in self.tools]})
        if method != "tools/call":
            return self._error(request_id, -32601, "method_not_found")
        params = request.get("params") if isinstance(request.get("params"), Mapping) else {}
        name = str(params.get("name") or "")
        if name not in self.handlers:
            return self._error(request_id, -32602, "unknown_tool")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), Mapping) else {}
        try:
            value = self.handlers[name](arguments)
            if inspect.isawaitable(value):
                value = await value
            return self._ok(request_id, text_result(value, is_error=isinstance(value, Mapping) and value.get("success") is False))
        except (TypeError, ValueError, KeyError) as exc:
            return self._ok(request_id, text_result({"success": False, "status": "invalid_request", "error": str(exc)}, is_error=True))
        except Exception as exc:
            # Never send exception reprs that may contain page data, paths or
            # credentials to the caller.  The domain service owns local logs.
            return self._ok(request_id, text_result({"success": False, "status": "failed", "error": type(exc).__name__}, is_error=True))

    @staticmethod
    def _ok(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def serve_stdio(dispatcher: McpJsonRpcDispatcher, *, reader=None, writer=None) -> None:
    """Serve one JSON object per line; diagnostics belong on stderr only."""

    reader = reader or sys.stdin
    writer = writer or sys.stdout
    while True:
        line = await asyncio.to_thread(reader.readline)
        if not line:
            return
        try:
            request = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse_error"}}
        else:
            response = await dispatcher.dispatch(request)
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            writer.flush()


class _LoopbackHandler(BaseHTTPRequestHandler):
    dispatcher: McpJsonRpcDispatcher

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        if self.path.rstrip("/") != "/mcp":
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get("content-length", "0")), 1_000_000)
            request = json.loads(self.rfile.read(length))
            response = asyncio.run(self.dispatcher.dispatch(request))
        except Exception:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid_request"}}
        body = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve_loopback(dispatcher: McpJsonRpcDispatcher, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Start a loopback-only server; caller owns its lifecycle."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("mcp_must_bind_loopback")
    handler = type("CommerceMcpHandler", (_LoopbackHandler,), {"dispatcher": dispatcher})
    server = ThreadingHTTPServer((host, int(port)), handler)
    return server


__all__ = ["McpJsonRpcDispatcher", "McpToolSpec", "serve_loopback", "serve_stdio", "text_result"]
