"""OAK camera discovery via DepthAI (USB and PoE/Ethernet)."""

from __future__ import annotations

import logging

from nilo_node.camera.device_connect import (
    device_display_name,
    device_ip,
    device_mxid,
    is_poe_protocol,
    iter_available_devices,
)
from nilo_node.camera.models import CameraDeviceInfo

logger = logging.getLogger(__name__)


def depthai_available() -> bool:
    try:
        import depthai  # noqa: F401

        return True
    except ImportError:
        return False


def _protocol_name(info: object) -> str:
    proto = getattr(info, "protocol", "")
    if hasattr(proto, "name"):
        return str(proto.name)
    return str(proto)


def discover_devices() -> list[CameraDeviceInfo]:
    """Return OAK devices on USB or PoE (same L3 subnet as host)."""
    if not depthai_available():
        logger.debug("DepthAI not installed — camera discovery unavailable")
        return []

    import depthai as dai

    devices: list[CameraDeviceInfo] = []
    try:
        for info in iter_available_devices(dai):
            protocol = _protocol_name(info)
            ip = device_ip(info) or ""
            mxid = device_mxid(info)
            devices.append(
                CameraDeviceInfo(
                    device_id=mxid,
                    name=device_display_name(info),
                    platform=str(getattr(info, "platform", "")),
                    protocol=protocol,
                    state="poe" if is_poe_protocol(protocol) or ip else "usb",
                )
            )
            if ip:
                logger.debug("OAK %s at %s (%s)", mxid[:8], ip, protocol)
    except Exception as exc:
        logger.warning("Camera discovery failed: %s", exc)
    return devices
