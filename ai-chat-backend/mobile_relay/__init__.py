"""后台手机 Relay 的连接与投递边界。

WS handler 只负责把协议消息适配到这里；连接是否在线、最后一次确认时间
以及后续的投递队列不应继续散落在 ``main.py`` 的全局变量里。
"""

from .liveness import (
    MobileRelayRegistry,
    get_mobile_relay_registry,
)
from .hub import MobileRelayHub, get_mobile_relay_hub

__all__ = [
    "MobileRelayRegistry",
    "get_mobile_relay_registry",
    "MobileRelayHub",
    "get_mobile_relay_hub",
]
