"""Prepare host WiFi interface for AP mode (NetworkManager, rfkill, regdomain, uap0)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApInterfacePlan:
    """Which NIC hostapd should bind to and how it was chosen."""

    sta_interface: str
    ap_interface: str
    mode: str  # concurrent | dedicated


async def _run_cmd(
    cmd: list[str],
    *,
    optional: bool = False,
) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        if optional:
            return 1, f"{cmd[0]} not found"
        raise
    stdout, _ = await proc.communicate()
    text = stdout.decode(errors="replace").strip()
    if proc.returncode != 0 and not optional:
        raise RuntimeError(text or f"Command failed: {' '.join(cmd)}")
    if proc.returncode != 0 and optional:
        logger.debug("%s failed (optional): %s", cmd[0], text)
    return proc.returncode or 0, text


async def release_wifi_from_network_manager(
    iface: str,
    *,
    disconnect: bool = True,
) -> None:
    """Mark interface unmanaged; optionally disconnect STA first."""
    nmcli = shutil.which("nmcli")
    if not nmcli:
        logger.debug("nmcli not found — skip NetworkManager release")
        return
    await _run_cmd([nmcli, "radio", "wifi", "on"], optional=True)
    if disconnect:
        await _run_cmd([nmcli, "device", "disconnect", iface], optional=True)
    code, text = await _run_cmd(
        [nmcli, "device", "set", iface, "managed", "no"],
        optional=True,
    )
    if code == 0:
        logger.info("WiFi interface %s released from NetworkManager", iface)
    else:
        logger.warning("Could not set %s unmanaged in NetworkManager: %s", iface, text)


async def _interface_exists(iface: str) -> bool:
    return Path(f"/sys/class/net/{iface}").exists()


async def _create_virtual_ap(sta_iface: str, ap_iface: str) -> bool:
    if await _interface_exists(ap_iface):
        return True
    iw = shutil.which("iw")
    if not iw:
        return False
    code, text = await _run_cmd(
        [iw, "dev", sta_iface, "interface", "add", ap_iface, "type", "__ap"],
        optional=True,
    )
    if code != 0:
        logger.warning(
            "Virtual AP %s on %s not supported by driver: %s",
            ap_iface,
            sta_iface,
            text,
        )
        return False
    logger.info("Created virtual AP interface %s on %s", ap_iface, sta_iface)
    return True


async def plan_ap_interface(
    sta_iface: str,
    ap_iface: str,
    *,
    concurrent_sta_ap: bool,
    country_code: str,
) -> ApInterfacePlan:
    """
    NiloCardmed-style: uap0 for AP + wlp3s0 stays STA when the driver allows it.
    Fallback: dedicated AP on the physical interface (disconnect STA).
    """
    rfkill = shutil.which("rfkill")
    if rfkill:
        await _run_cmd([rfkill, "unblock", "wifi"], optional=True)

    iw = shutil.which("iw")
    if iw:
        await _run_cmd([iw, "reg", "set", country_code], optional=True)

    ap_name = (ap_iface or "").strip()
    if concurrent_sta_ap and ap_name and ap_name != sta_iface:
        if await _create_virtual_ap(sta_iface, ap_name):
            await release_wifi_from_network_manager(ap_name, disconnect=False)
            ip_cmd = shutil.which("ip")
            if ip_cmd:
                await _run_cmd([ip_cmd, "link", "set", ap_name, "up"], optional=True)
            return ApInterfacePlan(sta_iface, ap_name, "concurrent")

    logger.info(
        "Using dedicated AP on %s (STA will disconnect if connected)",
        sta_iface,
    )
    await release_wifi_from_network_manager(sta_iface, disconnect=True)
    return ApInterfacePlan(sta_iface, sta_iface, "dedicated")


async def verify_ap_mode(iface: str) -> str | None:
    """Return error message if interface is not in AP mode after hostapd start."""
    iw = shutil.which("iw")
    if not iw:
        return None
    code, text = await _run_cmd([iw, "dev", iface, "info"], optional=True)
    if code != 0:
        return text or f"Could not query interface {iface}"
    if "type AP" in text:
        return None
    return (
        f"Interface {iface} is not in AP mode after hostapd start "
        f"(NetworkManager may still control it). iw output: {text}"
    )
