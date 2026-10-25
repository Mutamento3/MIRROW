"""Shared, deliberately small Android device boundary.

The commerce and Xiaohongshu domains are allowed to use one physical phone,
but they must never each grow their own unrestricted ADB wrapper.  This
package is the only place where an ADB command may be assembled.  Domain
modules receive deterministic primitives (``tap``, ``ui_dump``, ...), not a
shell and not the device serial.
"""

from .gateway import (
    AdbCommandResult,
    AdbDeviceGateway,
    DeviceBusyError,
    DeviceGateway,
    DeviceLease,
    DeviceLeaseManager,
    InMemoryDeviceGateway,
    LeaseUnavailableError,
)

__all__ = [
    "AdbCommandResult",
    "AdbDeviceGateway",
    "DeviceBusyError",
    "DeviceGateway",
    "DeviceLease",
    "DeviceLeaseManager",
    "InMemoryDeviceGateway",
    "LeaseUnavailableError",
]
