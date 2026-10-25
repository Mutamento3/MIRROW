"""统一的 mobile Relay 连接选路与请求回传。

WS handler 只负责协议适配；连接是否可用、请求发给哪个客户端以及回传
future 的生命周期都由这里维护。在线判断复用 :mod:`mobile_relay.liveness`
的心跳租约，避免 ``main.py`` 里另维护一份可能过期的 websocket 列表。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import uuid
from typing import Any, Dict, Iterable, Optional

from .liveness import (
    MobileRelayRegistry,
    RelayConnection,
    get_mobile_relay_registry,
)


@dataclass
class _PendingRelay:
    future: asyncio.Future
    websocket: Any
    connection_id: str


class MobileRelayHub:
    """单用户实例的 Relay 路由中心。

    原生 Service 是后台工具的首选，WebView 只在原生不可用时作为回退。
    ``MobileRelayRegistry`` 是连接新鲜度的唯一事实来源；``_tracked`` 只
    记录本 hub 接管的 WS，避免把其它协议端点误当成可发送的 Relay。
    """

    TOOL_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        registry: Optional[MobileRelayRegistry] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.registry = registry or get_mobile_relay_registry()
        self._logger = logger or logging.getLogger(__name__)
        self._tracked: Dict[int, Any] = {}
        self._pending: Dict[str, _PendingRelay] = {}

    def register(
        self,
        websocket: Any,
        *,
        client_kind: str = "webview",
        capabilities: Iterable[str] = (),
    ) -> RelayConnection:
        """登记一个由 hub 接管的 WS，并返回在线租约条目。"""

        entry = self.registry.register(
            websocket,
            client_kind=client_kind,
            capabilities=capabilities,
        )
        self._tracked[id(websocket)] = websocket
        return entry

    def update_hello(
        self,
        websocket: Any,
        *,
        client_kind: Optional[str] = None,
        capabilities: Optional[Iterable[str]] = None,
    ) -> Optional[RelayConnection]:
        return self.registry.update_hello(
            websocket,
            client_kind=client_kind,
            capabilities=capabilities,
        )

    def touch(
        self,
        websocket: Any,
        *,
        heartbeat: bool = False,
        tool_result: bool = False,
    ) -> Optional[RelayConnection]:
        return self.registry.touch(
            websocket,
            heartbeat=heartbeat,
            tool_result=tool_result,
        )

    def unregister(self, websocket: Any, reason: str = "closed") -> Optional[RelayConnection]:
        """只注销指定 WS，并唤醒发往该连接的等待请求。"""

        self._tracked.pop(id(websocket), None)
        entry = self.registry.unregister(websocket, reason)
        for request_id, pending in list(self._pending.items()):
            if pending.websocket is not websocket:
                continue
            self._pending.pop(request_id, None)
            if not pending.future.done():
                pending.future.set_exception(
                    ConnectionError(f"relay connection {pending.connection_id} closed")
                )
            # ``send_tool`` may fail while ``send_json`` is still in progress,
            # before it starts awaiting this future.  Mark the exception as
            # retrieved so that this cleanup path does not emit an unhandled
            # Future warning; a concurrent waiter still receives the same
            # exception from the future.
            try:
                pending.future.exception()
            except (asyncio.CancelledError, Exception):
                pass
        return entry

    @staticmethod
    def _supports_tool(entry: RelayConnection, tool: Optional[str]) -> bool:
        """按具体能力过滤，同时保留旧版只声明 generic 能力的连接。"""

        if not tool:
            return True
        declared_tools = {
            capability[5:]
            for capability in entry.capabilities
            if capability.startswith("tool:") and capability[5:]
        }
        # 没有任何 tool:<name> 声明的旧客户端仍按历史逻辑尝试。
        return not declared_tools or tool in declared_tools

    def _live_entries(self, tool: Optional[str] = None) -> list[RelayConnection]:
        """返回 fresh 连接，按原生优先并按具体能力过滤。"""

        entries = [
            entry
            for entry in self.registry.live_entries()
            if self._tracked.get(id(entry.websocket)) is entry.websocket
            and self._supports_tool(entry, tool)
        ]
        entries.sort(
            key=lambda entry: (
                0 if entry.client_kind == "native_service" else 1,
                -entry.last_heartbeat_at,
                -entry.connected_at,
            )
        )
        return entries

    def has_live_connection(self) -> bool:
        return bool(self._live_entries())

    @staticmethod
    def _sub_request_id(request_id: str, ordinal: int) -> str:
        base = str(request_id or uuid.uuid4().hex)
        return f"{base}__relay_{ordinal}_{uuid.uuid4().hex[:8]}"

    async def send_tool(self, request_id: str, tool: str, params: dict) -> dict:
        """把工具请求发给当前 fresh 连接，失败后按顺序尝试回退连接。"""

        entries = self._live_entries(tool)
        if not entries:
            return {"success": False, "error": "no mobile connection"}

        last_result: Optional[dict] = None
        attempted: list[str] = []
        for ordinal, entry in enumerate(entries):
            relay_request_id = self._sub_request_id(request_id, ordinal)
            loop = asyncio.get_running_loop()
            pending = _PendingRelay(
                future=loop.create_future(),
                websocket=entry.websocket,
                connection_id=entry.connection_id,
            )
            self._pending[relay_request_id] = pending
            attempted.append(f"{entry.client_kind}:{entry.connection_id[:8]}")
            try:
                await entry.websocket.send_json(
                    {
                        "type": "mobile_tool_request",
                        "request_id": relay_request_id,
                        "tool": tool,
                        "params": params,
                    }
                )
                result = await asyncio.wait_for(
                    pending.future,
                    timeout=self.TOOL_TIMEOUT_SECONDS,
                )
                if isinstance(result, dict):
                    last_result = result
                    # An executed plugin with an unreadable receipt is not safe to replay.
                    if result.get("execution_status") == "unknown":
                        return {**result, "success": False}
                    if result.get("success") is True:
                        return result
            except asyncio.TimeoutError:
                self._logger.debug(
                    "mobile relay timeout: tool=%s client=%s",
                    tool,
                    entry.connection_id[:8],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 发送层已确认该 socket 不可用，立即从租约与 hub 中一并移除。
                self._logger.info(
                    "mobile relay send failed: tool=%s client=%s error=%s",
                    tool,
                    entry.connection_id[:8],
                    type(exc).__name__,
                )
                self.unregister(entry.websocket, "relay_send_failed")
            finally:
                if self._pending.get(relay_request_id) is pending:
                    self._pending.pop(relay_request_id, None)

        self._logger.warning(
            "mobile relay exhausted: tool=%s attempted=%s",
            tool,
            ",".join(attempted),
        )
        if isinstance(last_result, dict):
            return last_result
        return {"success": False, "error": "all mobile WS failed"}

    async def send_payload(self, payload: dict) -> bool:
        """向当前 fresh 连接发送无需响应的主动推送。"""

        for entry in self._live_entries():
            try:
                await entry.websocket.send_json(payload)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.info(
                    "mobile relay payload failed: client=%s error=%s",
                    entry.connection_id[:8],
                    type(exc).__name__,
                )
                self.unregister(entry.websocket, "payload_send_failed")
        return False

    def resolve_tool_result(
        self,
        request_id: str,
        result: Any,
        *,
        top_success: Optional[bool] = None,
        error: Optional[str] = None,
    ) -> bool:
        """把 WS 回传交给对应请求；返回是否由 hub 消费。"""

        pending = self._pending.pop(str(request_id or ""), None)
        if pending is None:
            return False

        normalized = dict(result) if isinstance(result, dict) else {}
        # 内嵌 result.success 是新协议权威；仅兼容旧客户端缺失该字段的情况。
        if "success" not in normalized and top_success is not None:
            normalized["success"] = top_success
        if not isinstance(normalized.get("success"), bool):
            normalized.update(
                success=False, execution_status="unknown",
                error="mobile execution receipt has no boolean success",
            )
        if (
            top_success is False
            and error
            and not normalized.get("error")
            and not normalized.get("content")
        ):
            normalized["error"] = str(error)
        if not pending.future.done():
            pending.future.set_result(normalized)
        return True


_hub = MobileRelayHub()


def get_mobile_relay_hub() -> MobileRelayHub:
    return _hub
