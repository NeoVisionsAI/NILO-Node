"""OAK camera discovery via DepthAI (USB and PoE/Ethernet)."""

from __future__ import annotations

import logging

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


def _is_poe_protocol(protocol: str) -> bool:
    upper = protocol.upper()
    return "TCP" in upper or "IP" in upper


def discover_devices() -> list[CameraDeviceInfo]:
    """Return OAK devices on USB or PoE (same L3 subnet as host)."""
    if not depthai_available():
        logger.debug("DepthAI not installed — camera discovery unavailable")
        return []

    import depthai as dai

    devices: list[CameraDeviceInfo] = []
    try:
        for info in dai.Device.getAllAvailableDevices():
            protocol = _protocol_name(info)
            ip = ""
            for attr in ("getIpAddress", "getIP"):
                fn = getattr(info, attr, None)
                if callable(fn):
                    try:
                        ip = str(fn() or "")
                        break
                    except Exception:
                        pass
            devices.append(
                CameraDeviceInfo(
                    device_id=info.getMxId(),
                    name=info.name if hasattr(info, "name") else "OAK Camera",
                    platform=str(getattr(info, "platform", "")),
                    protocol=protocol,
                    state="poe" if _is_poe_protocol(protocol) else "usb",
                )
            )
            if ip:
                logger.debug("OAK %s at %s (%s)", info.getMxId()[:8], ip, protocol)
    except Exception as exc:
        logger.warning("Camera discovery failed: %s", exc)
    return devices
