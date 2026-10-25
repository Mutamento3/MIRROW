"""MIRROW 本地部署的客户端访问判定。

远程 Tailscale 客户端继续使用精确 IP；家庭局域网中的手机以固定网段和
该 WiFi 下稳定的设备 MAC 共同确认，从而不把 DHCP 租约误当成设备身份。
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


NeighborResolver = Callable[[str], Optional[str]]
_MAC_RE = re.compile(r"(?i)\b([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})\b")


def normalize_mac(value: str) -> str:
    match = _MAC_RE.search(value or "")
    return match.group(1).replace("-", ":").lower() if match else ""


def resolve_neighbor_mac(client_ip: str) -> Optional[str]:
    """从操作系统邻居表读取客户端 MAC；不使用 shell，也不记录原始输出。"""
    try:
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["arp", "-a", client_ip],
                capture_output=True,
                text=True,
                # Windows headings may use a legacy code page even under
                # Python UTF-8 mode. IP/MAC tokens are ASCII; replace only
                # undecodable headings rather than failing the reader thread.
                encoding="utf-8",
                errors="replace",
                timeout=1.5,
                check=False,
                creationflags=creationflags,
            )
            for line in (result.stdout or "").splitlines():
                if client_ip in line:
                    mac = normalize_mac(line)
                    if mac:
                        return mac
            return None

        result = subprocess.run(
            ["ip", "neigh", "show", client_ip],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        return normalize_mac(result.stdout) or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class NetworkAccessPolicy:
    exact_ips: frozenset[str]
    mobile_lan_mac: str = ""
    lan_networks: tuple[ipaddress._BaseNetwork, ...] = ()
    neighbor_resolver: NeighborResolver = resolve_neighbor_mac
    cache_ttl_seconds: float = 15.0
    _cache: dict[str, tuple[str, float]] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @classmethod
    def from_environment(cls, exact_ips: Iterable[str]) -> "NetworkAccessPolicy":
        networks = []
        for raw in os.getenv("MOBILE_LAN_CIDR", "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                network = ipaddress.ip_network(raw, strict=False)
            except ValueError:
                continue
            if network.is_private:
                networks.append(network)
        return cls(
            exact_ips=frozenset(exact_ips),
            mobile_lan_mac=normalize_mac(os.getenv("MOBILE_LAN_MAC", "")),
            lan_networks=tuple(networks),
        )

    def allows(self, client_ip: Optional[str]) -> bool:
        if not client_ip:
            return False
        # 空配置只允许本机；远程客户端必须显式登记。
        if client_ip in {'127.0.0.1', '::1'}:
            return True
        if client_ip in self.exact_ips:
            return True
        if not self.mobile_lan_mac or not self.lan_networks:
            return False
        try:
            address = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        if not any(address in network for network in self.lan_networks):
            return False
        return self._neighbor_mac(client_ip) == self.mobile_lan_mac

    def _neighbor_mac(self, client_ip: str) -> str:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(client_ip)
            if cached and now - cached[1] <= self.cache_ttl_seconds:
                return cached[0]
        mac = normalize_mac(self.neighbor_resolver(client_ip) or "")
        if mac:
            with self._lock:
                self._cache[client_ip] = (mac, now)
        return mac
