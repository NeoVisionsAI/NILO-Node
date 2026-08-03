"""OAK camera discovery via DepthAI."""

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


def discover_devices() -> list[CameraDeviceInfo]:
    """Return OAK devices visible on USB. Empty list if DepthAI unavailable."""
    if not depthai_available():
        logger.debug("DepthAI not installed — camera discovery unavailable")
        return []

    import depthai as dai

    devices: list[CameraDeviceInfo] = []
    try:
        for info in dai.Device.getAllAvailableDevices():
            devices.append(
                CameraDeviceInfo(
                    device_id=info.getMxId(),
                    name=info.name if hasattr(info, "name") else "OAK Camera",
                    platform=str(getattr(info, "platform", "")),
                    protocol=str(getattr(info, "protocol", "")),
                )
            )
    except Exception as exc:
        logger.warning("Camera discovery failed: %s", exc)
    return devices
