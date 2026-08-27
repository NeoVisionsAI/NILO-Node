"""Prepare host WiFi interface for AP mode (NetworkManager, rfkill, regdomain, uap0)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApInterfacePlan:
    """Which NIC hostapd should bind to and how it was chosen."""

    sta_interface: str
    ap_interface: str
    mode: str  # concurrent | dedicated
    channel: int | None = None


def rtnetlink_error_is_benign(message: str) -> bool:
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "file exists",
            "name not unique",
            "already exists",
        )
    )


def freq_to_channel(freq_mhz: int) -> int | None:
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2412) // 5 + 1
    if freq_mhz == 2484:
        return 14
    return None


def detect_operating_channel(sta_iface: str) -> int | None:
    """Return WiFi channel used by STA (required for concurrent AP+STA)."""
    iw = shutil.which("iw")
    if not iw:
        return None
    try:
        result = subprocess.run(
            [iw, "dev", sta_iface, "link"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if "freq:" not in line.lower():
            continue
        match = re.search(r"freq:\s*(\d+)", line, re.IGNORECASE)
        if not match:
            continue
        channel = freq_to_channel(int(match.group(1)))
        if channel is not None:
            logger.info("STA %s on channel %d — AP will match", sta_iface, channel)
            return channel
    return None


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


async def _interface_exists(iface: str) -> bool:
    return Path(f"/sys/class/net/{iface}").exists()


async def teardown_virtual_ap(ap_iface: str) -> None:
    """Remove virtual AP interface (safe if missing)."""
    iw = shutil.which("iw")
    if not iw or not await _interface_exists(ap_iface):
        return
    logger.info("Removing virtual AP interface %s", ap_iface)
    await _run_cmd([iw, "dev", ap_iface, "del"], optional=True)
    await asyncio.sleep(0.5)


async def release_wifi_from_network_manager(
    iface: str,
    *,
    disconnect: bool = True,
) -> None:
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return
    await _run_cmd([nmcli, "radio", "wifi", "on"], optional=True)
    if disconnect:
        await _run_cmd([nmcli, "device", "disconnect", iface], optional=True)
    await _run_cmd([nmcli, "device", "set", iface, "managed", "no"], optional=True)


async def _create_virtual_ap(sta_iface: str, ap_iface: str) -> bool:
    iw = shutil.which("iw")
    if not iw:
        return False

    if await _interface_exists(ap_iface):
        logger.info("Reusing existing virtual AP %s", ap_iface)
        return True

    code, text = await _run_cmd(
        [iw, "dev", sta_iface, "interface", "add", ap_iface, "type", "__ap"],
        optional=True,
    )
    if code == 0:
        logger.info("Created virtual AP %s on %s", ap_iface, sta_iface)
        return True

    if rtnetlink_error_is_benign(text):
        await asyncio.sleep(0.5)
        if await _interface_exists(ap_iface):
            logger.info("Virtual AP %s present after RTNETLINK conflict — reusing", ap_iface)
            return True
        await teardown_virtual_ap(ap_iface)
        await asyncio.sleep(0.3)
        code2, text2 = await _run_cmd(
            [iw, "dev", sta_iface, "interface", "add", ap_iface, "type", "__ap"],
            optional=True,
        )
        if code2 == 0 or await _interface_exists(ap_iface):
            return True
        text = text2 or text

    logger.warning("Virtual AP %s on %s unavailable: %s", ap_iface, sta_iface, text)
    return False


async def configure_ap_interface_ip(ap_iface: str, ap_ip: str, prefix: int) -> None:
    ip_cmd = shutil.which("ip")
    if not ip_cmd:
        return
    cidr = f"{ap_ip}/{prefix}"
    for cmd in (
        [ip_cmd, "link", "set", ap_iface, "up"],
        [ip_cmd, "addr", "flush", "dev", ap_iface],
        [ip_cmd, "addr", "replace", cidr, "dev", ap_iface],
    ):
        _, err = await _run_cmd(cmd, optional=True)
        if err and not rtnetlink_error_is_benign(err):
            logger.debug("AP IP setup (%s): %s", " ".join(cmd[2:]), err)


async def plan_ap_interface(
    sta_iface: str,
    ap_iface: str,
    *,
    concurrent_sta_ap: bool,
    country_code: str,
    ap_ip: str,
    netmask_prefix: int,
    default_channel: int,
) -> ApInterfacePlan:
    rfkill = shutil.which("rfkill")
    if rfkill:
        await _run_cmd([rfkill, "unblock", "wifi"], optional=True)

    iw = shutil.which("iw")
    if iw:
        await _run_cmd([iw, "reg", "set", country_code], optional=True)

    channel = detect_operating_channel(sta_iface) or default_channel
    ap_name = (ap_iface or "").strip()

    if concurrent_sta_ap and ap_name and ap_name != sta_iface:
        await teardown_virtual_ap(ap_name)
        if await _create_virtual_ap(sta_iface, ap_name):
            await release_wifi_from_network_manager(ap_name, disconnect=False)
            await configure_ap_interface_ip(ap_name, ap_ip, netmask_prefix)
            return ApInterfacePlan(sta_iface, ap_name, "concurrent", channel)

    logger.info("Using dedicated AP on %s", sta_iface)
    await release_wifi_from_network_manager(sta_iface, disconnect=True)
    await configure_ap_interface_ip(sta_iface, ap_ip, netmask_prefix)
    return ApInterfacePlan(sta_iface, sta_iface, "dedicated", channel)


async def verify_ap_mode(iface: str) -> str | None:
    iw = shutil.which("iw")
    if not iw:
        return None
    code, text = await _run_cmd([iw, "dev", iface, "info"], optional=True)
    if code != 0:
        return text or f"Could not query interface {iface}"
    if "type AP" in text:
        return None
    return (
        f"Interface {iface} is not in AP mode after hostapd start. "
        f"Try: iw dev {iface} del && restart WiFi AP. iw: {text}"
    )
