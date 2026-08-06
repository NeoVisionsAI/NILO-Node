"""DepthAI device discovery and connection (USB + PoE/Ethernet)."""

from __future__ import annotations

import logging
from typing import Any

from nilo_node.camera.oak_settings import (
    load_oak_connection_settings,
    persist_discovered_oak_settings,
)

logger = logging.getLogger(__name__)

DEFAULT_POE_CAMERA_IP = "169.254.1.222"
DEFAULT_POE_HOST_IP = "169.254.1.10"


def uses_depthai_v2(dai: Any) -> bool:
    """DepthAI 2.x exposes XLinkOut; v3 removed it."""
    return hasattr(dai.node, "XLinkOut")


def _normalize_ip(ip: str) -> str:
    return ip.strip().replace("..", ".")


def device_info_for_ip(dai: Any, ip: str) -> Any:
    """Build DeviceInfo for PoE/TCP — v3 accepts IP string only; v2 may need XLinkProtocol."""
    ip = _normalize_ip(ip)
    if uses_depthai_v2(dai):
        xlink = getattr(dai, "XLinkProtocol", None)
        if xlink is not None and hasattr(xlink, "X_LINK_TCP_IP"):
            return dai.DeviceInfo(ip, xlink.X_LINK_TCP_IP)
    return dai.DeviceInfo(ip)


def device_info_for_id(dai: Any, device_id: str) -> Any:
    return dai.DeviceInfo(device_id)


def _protocol_name(info: Any) -> str:
    proto = getattr(info, "protocol", "")
    if hasattr(proto, "name"):
        return str(proto.name)
    return str(proto)


def device_ip(info: Any) -> str | None:
    for attr in ("getIpAddress", "getIP"):
        fn = getattr(info, attr, None)
        if callable(fn):
            try:
                ip = fn()
                if ip:
                    return str(ip)
            except Exception:
                pass
    name = getattr(info, "name", None)
    if isinstance(name, str) and name and name[0].isdigit():
        return name
    return None


def device_mxid(info: Any) -> str:
    """MxId across DepthAI 2.x (getMxId) and 3.x (mxid / getDeviceId)."""
    for method in ("getMxId", "getDeviceId"):
        fn = getattr(info, method, None)
        if callable(fn):
            try:
                value = fn()
                if value:
                    return str(value)
            except Exception:
                pass
    for attr in ("mxid", "deviceId", "name"):
        value = getattr(info, attr, None)
        if value:
            return str(value)
    return "unknown"


def device_display_name(info: Any) -> str:
    name = getattr(info, "name", None)
    if name:
        return str(name)
    return "OAK"


def iter_available_devices(dai: Any) -> list[Any]:
    """Enumerate devices using Bootloader API (preferred) or Device API."""
    for source in (
        getattr(dai, "DeviceBootloader", None),
        getattr(dai, "Device", None),
    ):
        if source is None:
            continue
        getter = getattr(source, "getAllAvailableDevices", None)
        if not callable(getter):
            continue
        try:
            found = list(getter())
            if found:
                return found
        except Exception as exc:
            logger.debug("Device enumeration via %s failed: %s", source.__name__, exc)
    return []


def is_poe_protocol(protocol: str) -> bool:
    upper = protocol.upper()
    return "TCP" in upper or "IP" in upper or "POE" in upper


def list_devices() -> list[dict[str, str]]:
    import depthai as dai

    devices: list[dict[str, str]] = []
    for info in iter_available_devices(dai):
        protocol = _protocol_name(info)
        ip = device_ip(info) or ""
        mxid = device_mxid(info)
        devices.append(
            {
                "mxid": mxid,
                "name": device_display_name(info),
                "protocol": protocol,
                "ip": ip,
                "connection": "poe" if is_poe_protocol(protocol) or ip else "usb",
            }
        )
    return devices


def resolve_device_info(
    dai: Any,
    *,
    device_id: str | None = None,
    device_ip: str | None = None,
    prefer: str = "auto",
) -> tuple[Any, dict[str, str]]:
    """Resolve DeviceInfo for USB or PoE. prefer: auto | usb | poe."""
    settings = load_oak_connection_settings(
        device_ip=device_ip,
        device_id=device_id,
        prefer=prefer,
    )
    device_ip = settings.device_ip or None
    device_id = settings.device_id or None
    prefer = settings.connection_mode or prefer

    if device_ip:
        device_ip = _normalize_ip(device_ip)
        logger.info("Connecting to OAK by IP (PoE): %s", device_ip)
        info = device_info_for_ip(dai, device_ip)
        meta = {"mxid": device_id or device_ip, "ip": device_ip, "protocol": "TCP", "connection": "poe"}
        persist_discovered_oak_settings(meta)
        return info, meta

    available = list_devices()
    if not available:
        if prefer in ("poe", "auto") and not device_id:
            fallback_ip = device_ip or DEFAULT_POE_CAMERA_IP
            logger.info("No devices discovered — trying default PoE IP %s", fallback_ip)
            return resolve_device_info(
                dai,
                device_id=device_id,
                device_ip=fallback_ip,
                prefer=prefer,
            )
        raise RuntimeError(
            "No OAK device found. PoE: set host Ethernet to 169.254.1.10/16 "
            f"(camera usually {DEFAULT_POE_CAMERA_IP}). See docs/POE_SETUP.md"
        )

    chosen: dict[str, str] | None = None

    if device_id:
        for d in available:
            if d["mxid"] == device_id or d["mxid"].startswith(device_id):
                chosen = d
                break
        if chosen is None:
            raise RuntimeError(f"Device {device_id} not found. Available: {available}")

    if chosen is None and prefer == "poe":
        for d in available:
            if d["connection"] == "poe":
                chosen = d
                break

    if chosen is None and prefer == "usb":
        for d in available:
            if d["connection"] == "usb":
                chosen = d
                break

    if chosen is None:
        chosen = available[0]

    mxid = chosen["mxid"]
    ip = chosen.get("ip") or ""

    if chosen["connection"] == "poe" and ip:
        info = device_info_for_ip(dai, ip)
    else:
        info = device_info_for_id(dai, mxid)

    logger.info("Selected OAK %s (%s) protocol=%s", mxid[:12], chosen["connection"], chosen["protocol"])
    persist_discovered_oak_settings(chosen)
    return info, chosen


def format_devices_report(devices: list[dict[str, str]]) -> str:
    if not devices:
        return "No devices found."
    lines = ["MXID           Connection  Protocol              IP"]
    for d in devices:
        lines.append(
            f"{d['mxid'][:14]:<14} {d['connection']:<10} {d['protocol']:<20} {d.get('ip') or '-'}"
        )
    return "\n".join(lines)


def should_use_depthai_hardware(
    *,
    depthai_ok: bool,
    devices: list,
    device_ip: str = "",
    connection_mode: str = "auto",
) -> bool:
    """Use real OAK capture when DepthAI is installed and PoE/IP is configured."""
    if not depthai_ok:
        return False
    if devices:
        return True
    if device_ip.strip():
        return True
    return connection_mode == "poe"
