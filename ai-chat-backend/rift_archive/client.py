"""Small fixed-surface Streamable HTTP MCP client for Rift Archive.

The remote server is treated as an untrusted game-state authority.  Tool
discovery is used only for compatibility checks; it never dynamically adds
tools to AI's prompt.
"""

from __future__ import annotations

import asyncio
import json
from itertools import count
from typing import Any, Mapping

import httpx


RIFT_MCP_URL = "https://rift.guchuan.men/mcp"
_REQUIRED_TOOLS = frozenset({
    "start_case", "get_case_state", "take_case_action", "read_case_archive",
})


class RiftMcpError(RuntimeError):
    pass


class RiftMcpAmbiguousWrite(RiftMcpError):
    """A write may have reached the server; callers must reconcile by reading."""


class RiftArchiveMcpClient:
    def __init__(self, *, endpoint: str = RIFT_MCP_URL, transport: httpx.AsyncBaseTransport | None = None):
        if endpoint != RIFT_MCP_URL:
            raise ValueError("rift_endpoint_is_fixed")
        self.endpoint = endpoint
        self._ids = count(1)
        self._ready = False
        self._init_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json, text/event-stream"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _rpc(self, method: str, params: Mapping[str, Any], *, write: bool = False) -> dict[str, Any]:
        request_id = next(self._ids)
        try:
            response = await self._http.post(
                self.endpoint,
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            if len(response.content) > 2 * 1024 * 1024:
                raise RiftMcpError("rift_response_too_large")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if write:
                raise RiftMcpAmbiguousWrite(type(exc).__name__) from exc
            raise RiftMcpError(type(exc).__name__) from exc
        except httpx.HTTPStatusError as exc:
            raise RiftMcpError(f"http_{exc.response.status_code}") from exc

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RiftMcpError("invalid_json_response") from exc
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            raise RiftMcpError("invalid_jsonrpc_response")
        if payload.get("error"):
            err = payload["error"]
            if isinstance(err, dict):
                raise RiftMcpError(str(err.get("message") or err.get("code") or "mcp_error"))
            raise RiftMcpError("mcp_error")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RiftMcpError("missing_mcp_result")
        return result

    async def ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            initialized = await self._rpc("initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mirrow-k", "version": "1.0.0"},
            })
            server = initialized.get("serverInfo") or {}
            if server.get("name") != "rift-archive":
                raise RiftMcpError("unexpected_mcp_server")
            listed = await self._rpc("tools/list", {})
            names = {
                str(tool.get("name")) for tool in (listed.get("tools") or [])
                if isinstance(tool, dict)
            }
            if not _REQUIRED_TOOLS.issubset(names):
                raise RiftMcpError("rift_tool_surface_incompatible")
            self._ready = True

    @staticmethod
    def _decode_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            raise RiftMcpError("remote_tool_error")
        for item in result.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                value = json.loads(text)
            except (ValueError, json.JSONDecodeError):
                return {"message": text[:4000]}
            if isinstance(value, dict):
                return value
            return {"value": value}
        return {}

    async def call_tool(self, name: str, arguments: Mapping[str, Any], *, write: bool = False) -> dict[str, Any]:
        if name not in _REQUIRED_TOOLS and name != "read_investigator":
            raise RiftMcpError("tool_not_allowed")
        await self.ensure_ready()
        result = await self._rpc(
            "tools/call", {"name": name, "arguments": dict(arguments)}, write=write,
        )
        return self._decode_tool_result(result)
