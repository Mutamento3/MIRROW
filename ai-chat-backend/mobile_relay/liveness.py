"""Mobile Relay 在线租约。

这里保存的是后端进程内的当前连接事实，不是手机感知数据的权威存储。
手机每次重连都会重新注册；后端重启后连接自然全部重新登记。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Dict, Iterable, Optional


@dataclass
class RelayConnection:
    connection_id: str
    websocket: Any = field(repr=False)
    client_kind: str
    connected_at: float
    last_rx_at: float
    last_heartbeat_at: float
    last_tool_result_at: float = 0.0
    capabilities: tuple[str, ...] = ()
    closed_at: float = 0.0
    close_reason: str = ""


class MobileRelayRegistry:
    """单用户实例的当前 mobile WS 注册表。

    ``online`` 只由原生 Relay 的租约决定；WebView 连接可以作为辅助状态，
    但不能证明后台 Service 仍能执行工具。
    """

    ONLINE_LEASE_SECONDS = 120.0
    STALE_LEASE_SECONDS = 180.0

    def __init__(self) -> None:
        self._connections: Dict[str, RelayConnection] = {}
        self._last_disconnect_at = 0.0
        self._last_disconnect_reason = ""

    def register(
        self,
        websocket: Any,
        *,
        client_kind: str = "webview",
        capabilities: Iterable[str] = (),
        connection_id: Optional[str] = None,
    ) -> RelayConnection:
        now = time.time()
        self._prune_expired(now)
        entry = RelayConnection(
            connection_id=connection_id or uuid.uuid4().hex,
            websocket=websocket,
            client_kind=client_kind or "webview",
            connected_at=now,
            last_rx_at=now,
            last_heartbeat_at=now,
            capabilities=tuple(str(v) for v in capabilities),
        )
        self._connections[entry.connection_id] = entry
        return entry

    def find(self, websocket: Any) -> Optional[RelayConnection]:
        for entry in self._connections.values():
            if entry.websocket is websocket:
                return entry
        return None

    def touch(
        self,
        websocket: Any,
        *,
        heartbeat: bool = False,
        tool_result: bool = False,
    ) -> Optional[RelayConnection]:
        entry = self.find(websocket)
        if entry is None:
            return None
        now = time.time()
        entry.last_rx_at = now
        if heartbeat:
            entry.last_heartbeat_at = now
        if tool_result:
            entry.last_tool_result_at = now
        return entry

    def update_hello(
        self,
        websocket: Any,
        *,
        client_kind: Optional[str] = None,
        capabilities: Optional[Iterable[str]] = None,
    ) -> Optional[RelayConnection]:
        entry = self.touch(websocket, heartbeat=True)
        if entry is None:
            return None
        if client_kind:
            entry.client_kind = str(client_kind)
        if capabilities is not None:
            entry.capabilities = tuple(str(v) for v in capabilities)
        return entry

    def unregister(self, websocket: Any, reason: str = "closed") -> Optional[RelayConnection]:
        entry = self.find(websocket)
        if entry is None:
            return None
        entry.closed_at = time.time()
        entry.close_reason = (reason or "closed")[:120]
        self._connections.pop(entry.connection_id, None)
        self._last_disconnect_at = entry.closed_at
        self._last_disconnect_reason = entry.close_reason
        return entry

    @staticmethod
    def _age(now: float, timestamp: float) -> Optional[float]:
        if not timestamp:
            return None
        return max(0.0, now - timestamp)

    def _is_fresh(self, entry: RelayConnection, now: float) -> bool:
        return self._age(now, entry.last_heartbeat_at) is not None and (
            now - entry.last_heartbeat_at <= self.ONLINE_LEASE_SECONDS
        )

    def _is_stale(self, entry: RelayConnection, now: float) -> bool:
        return self._age(now, entry.last_heartbeat_at) is not None and (
            now - entry.last_heartbeat_at <= self.STALE_LEASE_SECONDS
        )

    def _prune_expired(self, now: float) -> None:
        """Drop sockets whose lease expired without a clean WS close callback."""
        expired = [
            entry for entry in self._connections.values()
            if self._age(now, entry.last_heartbeat_at) is not None
            and now - entry.last_heartbeat_at > self.STALE_LEASE_SECONDS
        ]
        for entry in expired:
            self._connections.pop(entry.connection_id, None)
        if expired:
            self._last_disconnect_at = now
            self._last_disconnect_reason = "lease_expired"

    def live_entries(
        self,
        now: Optional[float] = None,
        *,
        client_kind: Optional[str] = None,
    ) -> list[RelayConnection]:
        """Return connections with a fresh heartbeat lease.

        Relay routing must use the same lease fact as the liveness endpoint;
        callers should not maintain a second websocket list for eligibility.
        """

        now = time.time() if now is None else now
        self._prune_expired(now)
        entries = [
            entry
            for entry in self._connections.values()
            if self._is_fresh(entry, now)
            and (client_kind is None or entry.client_kind == client_kind)
        ]
        return list(entries)

    def status(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        self._prune_expired(now)
        entries = list(self._connections.values())
        native = [e for e in entries if e.client_kind == "native_service"]
        webview = [e for e in entries if e.client_kind == "webview"]
        native_fresh = [e for e in native if self._is_fresh(e, now)]
        native_stale = [e for e in native if self._is_stale(e, now)]
        if native_fresh:
            state = "online"
        elif native_stale or webview:
            state = "degraded"
        elif entries:
            # 连接对象仍在列表中，但原生租约已经超过 stale 窗口；
            # 这不是可用的 WebView 降级，而是应等待本地看门狗恢复的离线事实。
            state = "offline"
        else:
            state = "offline"

        def public_entry(entry: RelayConnection) -> Dict[str, Any]:
            return {
                "connection_id": entry.connection_id,
                "client_kind": entry.client_kind,
                "connected_at": entry.connected_at,
                "last_rx_at": entry.last_rx_at,
                "last_heartbeat_at": entry.last_heartbeat_at,
                "last_tool_result_at": entry.last_tool_result_at or None,
                "heartbeat_age_seconds": self._age(now, entry.last_heartbeat_at),
                "capabilities": list(entry.capabilities),
            }

        return {
            "state": state,
            "native_online": bool(native_fresh),
            "native_count": len(native),
            "webview_count": len(webview),
            "online_lease_seconds": self.ONLINE_LEASE_SECONDS,
            "stale_lease_seconds": self.STALE_LEASE_SECONDS,
            "last_disconnect_at": self._last_disconnect_at or None,
            "last_disconnect_reason": self._last_disconnect_reason or None,
            "connections": [public_entry(e) for e in entries],
        }

    def is_native_online(self) -> bool:
        return bool(self.status().get("native_online"))

    def has_live_connection(self) -> bool:
        """Whether any mobile client has a fresh lease for legacy consumers."""
        now = time.time()
        self._prune_expired(now)
        return any(
            self._age(now, entry.last_heartbeat_at) is not None
            and now - entry.last_heartbeat_at <= self.ONLINE_LEASE_SECONDS
            for entry in self._connections.values()
        )


_registry = MobileRelayRegistry()


def get_mobile_relay_registry() -> MobileRelayRegistry:
    return _registry
