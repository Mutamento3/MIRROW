"""Compatibility exports for the shared Android gateway."""

from android_device_gateway.gateway import (
    DeviceBusyError,
    DeviceGateway,
    DeviceLeaseManager,
    LeaseUnavailableError,
)

__all__ = ["DeviceBusyError", "DeviceGateway", "DeviceLeaseManager", "LeaseUnavailableError"]
