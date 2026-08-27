"""Prepare host WiFi interface for AP mode (NetworkManager, rfkill, regdomain, uap0)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
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
    hw_mode: str = "g"  # g = 2.4 GHz, a = 5 GHz


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
    if 5170 <= freq_mhz <= 5825:
        return (freq_mhz - 5000) // 5
    return None


def hw_mode_for_channel(channel: int) -> str:
    return "g" if channel <= 14 else "a"


def detect_sta_is_connected(sta_iface: str) -> bool:
    iw = shutil.which("iw")
    if not iw:
        return False
    try:
        result = subprocess.run(
            [iw, "dev", sta_iface, "link"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    text = result.stdout
    if "Not connected" in text:
        return False
    return "Connected to" in text or "SSID:" in text


def detect_operating_channel(sta_iface: str) -> int | None:
    """Return WiFi channel used by STA (required for concurrent AP+STA, #channels <= 1)."""
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
    text = result.stdout
    match = re.search(r"channel\s+(\d+)", text, re.IGNORECASE)
    if match:
        channel = int(match.group(1))
        logger.info(
            "STA %s on channel %d (%s GHz) — AP will match",
            sta_iface,
            channel,
            "2.4" if channel <= 14 else "5",
        )
        return channel
    for line in text.splitlines():
        if "freq:" not in line.lower():
            continue
        freq_match = re.search(r"freq:\s*(\d+)", line, re.IGNORECASE)
        if not freq_match:
            continue
        channel = freq_to_channel(int(freq_match.group(1)))
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


async def _interface_type(iface: str) -> str | None:
    iw = shutil.which("iw")
    if not iw:
        return None
    code, text = await _run_cmd([iw, "dev", iface, "info"], optional=True)
    if code != 0:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "type":
            return parts[1]
    return None


def find_pids_by_cmdline(pattern: str) -> list[int]:
    """Find process PIDs whose cmdline contains pattern (works without pkill)."""
    pids: list[int] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return pids
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        text = raw.replace(b"\0", b" ").decode(errors="replace")
        if pattern in text:
            pids.append(int(entry.name))
    return pids


async def kill_processes_by_cmdline(pattern: str) -> None:
    """Terminate processes matching cmdline; SIGKILL survivors."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in find_pids_by_cmdline(pattern):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                logger.debug("No permission to signal pid %d", pid)
        if sig == signal.SIGTERM:
            await asyncio.sleep(0.4)


async def pkill_pattern(pattern: str) -> None:
    """Kill processes matching pattern; uses /proc scan, pkill optional."""
    await kill_processes_by_cmdline(pattern)
    pkill = shutil.which("pkill")
    if pkill:
        await _run_cmd([pkill, "-f", pattern], optional=True)
        await asyncio.sleep(0.2)


def resolve_ap_interface_name(sta_iface: str, configured: str) -> str:
    """Primary virtual AP name (avoid poisoned uap0 — use {sta}-ap by default)."""
    name = (configured or "").strip()
    if not name or name.lower() == "auto":
        return f"{sta_iface}-ap"
    return name


def ap_interface_candidates(sta_iface: str, configured: str) -> list[str]:
    """Names to try when creating virtual AP (driver may reject uap0 after failed attempts)."""
    ordered = [
        resolve_ap_interface_name(sta_iface, configured),
        f"{sta_iface}-ap",
        "niloap0",
        "uap0",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for name in ordered:
        if name and name != sta_iface and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _parse_iw_dev(text: str) -> list[dict[str, str | None]]:
    interfaces: list[dict[str, str | None]] = []
    current: dict[str, str | None] = {}
    current_phy: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("phy#"):
            current_phy = stripped.split("#", 1)[-1]
        elif stripped.startswith("Interface "):
            if current:
                interfaces.append(current)
            current = {"phy": current_phy, "name": stripped.split()[1], "type": None}
        elif stripped.startswith("type ") and current:
            current["type"] = stripped.split()[1]
    if current:
        interfaces.append(current)
    return interfaces


async def force_remove_iface(name: str) -> None:
    """Best-effort remove interface by name (handles half-deleted / ghost names)."""
    ip_cmd = shutil.which("ip")
    iw = shutil.which("iw")
    if ip_cmd:
        await _run_cmd([ip_cmd, "link", "set", name, "down"], optional=True)
        await _run_cmd([ip_cmd, "link", "delete", name, "type", "wlan"], optional=True)
        await _run_cmd([ip_cmd, "link", "delete", name], optional=True)
    if iw:
        await _run_cmd([iw, "dev", name, "del"], optional=True)


async def cleanup_phy_ap_interfaces(sta_iface: str) -> None:
    """Remove all AP virtual interfaces on the STA phy (driver allows only one)."""
    iw = shutil.which("iw")
    if not iw:
        return
    sta_phy = await _phy_name(sta_iface)
    code, text = await _run_cmd([iw, "dev"], optional=True)
    if code != 0:
        return
    for iface in _parse_iw_dev(text):
        name = iface.get("name")
        if not name or name == sta_iface:
            continue
        if sta_phy and iface.get("phy") != sta_phy:
            continue
        if iface.get("type") in ("AP", "__ap"):
            logger.info("Removing stale AP interface %s (phy %s)", name, sta_phy)
            await teardown_virtual_ap(name)
    for ghost in ap_interface_candidates(sta_iface, "auto"):
        await force_remove_iface(ghost)
    await asyncio.sleep(0.5)


async def _kill_nilo_hostapd(hostapd_conf: str = "/data/wifi/hostapd.conf") -> None:
    """Stop only NILO hostapd (by config path). Never pkill by interface name."""
    await pkill_pattern(hostapd_conf)


async def teardown_virtual_ap(ap_iface: str) -> None:
    """Remove virtual AP interface (safe if missing)."""
    await _kill_nilo_hostapd()
    if not await _interface_exists(ap_iface):
        await force_remove_iface(ap_iface)
        return
    ip_cmd = shutil.which("ip")
    if ip_cmd:
        await _run_cmd([ip_cmd, "link", "set", ap_iface, "down"], optional=True)
        await _run_cmd([ip_cmd, "addr", "flush", "dev", ap_iface], optional=True)
    iw = shutil.which("iw")
    if iw:
        logger.info("Removing virtual AP interface %s", ap_iface)
        for attempt in range(3):
            code, text = await _run_cmd([iw, "dev", ap_iface, "del"], optional=True)
            if code == 0 or not await _interface_exists(ap_iface):
                break
            if attempt < 2:
                await asyncio.sleep(0.5)
            elif text:
                logger.warning("Could not delete %s: %s", ap_iface, text)
    await force_remove_iface(ap_iface)
    await asyncio.sleep(0.3)


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


async def _phy_name(sta_iface: str) -> str | None:
    iw = shutil.which("iw")
    if not iw:
        return None
    code, text = await _run_cmd([iw, "dev", sta_iface, "info"], optional=True)
    if code != 0:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "wiphy":
            return f"phy{parts[1]}"
    return None


async def _create_virtual_ap_once(sta_iface: str, ap_iface: str) -> tuple[bool, str]:
    iw = shutil.which("iw")
    if not iw:
        return False, "iw not found"

    async def _try_add(args: list[str]) -> tuple[int, str]:
        return await _run_cmd(
            [iw, *args, "interface", "add", ap_iface, "type", "__ap"],
            optional=True,
        )

    code, text = await _try_add(["dev", sta_iface])
    if code == 0:
        logger.info("Created virtual AP %s on %s", ap_iface, sta_iface)
        return True, text

    phy = await _phy_name(sta_iface)
    if phy:
        code, text = await _try_add([phy])
        if code == 0:
            logger.info("Created virtual AP %s via %s", ap_iface, phy)
            return True, text

    return False, text


async def create_virtual_ap(sta_iface: str, configured_ap: str) -> str | None:
    """Create virtual AP; try several names if uap0 is poisoned in the driver."""
    await cleanup_phy_ap_interfaces(sta_iface)
    last_error = ""
    for ap_name in ap_interface_candidates(sta_iface, configured_ap):
        await teardown_virtual_ap(ap_name)
        await force_remove_iface(ap_name)
        await asyncio.sleep(0.3)
        ok, err = await _create_virtual_ap_once(sta_iface, ap_name)
        if ok and await _interface_exists(ap_name):
            iface_type = await _interface_type(ap_name)
            if iface_type in ("AP", "__ap"):
                logger.info("Using virtual AP interface %s", ap_name)
                return ap_name
        last_error = err
        if rtnetlink_error_is_benign(err):
            logger.warning("Virtual AP %s rejected (%s) — trying next name", ap_name, err)
        else:
            logger.warning("Virtual AP %s failed: %s", ap_name, err)
    logger.error(
        "Could not create virtual AP on %s (tried %s). Last: %s. Reboot may be required.",
        sta_iface,
        ap_interface_candidates(sta_iface, configured_ap),
        last_error,
    )
    return None


async def prepare_dedicated_ap(sta_iface: str) -> None:
    """Release STA from NetworkManager/wpa before hostapd on the physical NIC."""
    if detect_sta_is_connected(sta_iface):
        logger.warning(
            "STA %s still connected — dedicated AP may fail (key not allowed). "
            "Use concurrent_sta_ap: true + uap0, or disconnect WiFi client first.",
            sta_iface,
        )
    await release_wifi_from_network_manager(sta_iface, disconnect=True)
    iw = shutil.which("iw")
    if iw:
        await _run_cmd([iw, "dev", sta_iface, "disconnect"], optional=True)
    wpa_cli = shutil.which("wpa_cli")
    if wpa_cli:
        await _run_cmd([wpa_cli, "-i", sta_iface, "disconnect"], optional=True)
    ip_cmd = shutil.which("ip")
    if ip_cmd:
        await _run_cmd([ip_cmd, "link", "set", sta_iface, "down"], optional=True)
        await asyncio.sleep(1.5)
        await _run_cmd([ip_cmd, "link", "set", sta_iface, "up"], optional=True)
        await asyncio.sleep(1.0)
    if iw and not detect_sta_is_connected(sta_iface):
        await _run_cmd([iw, "dev", sta_iface, "set", "type", "__ap"], optional=True)


async def configure_ap_interface_ip(ap_iface: str, ap_ip: str, prefix: int) -> None:
    """Assign AP IP (interface should already be UP — matches wifi-ap-run.sh)."""
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


async def ensure_concurrent_ap_ready(
    sta_iface: str,
    ap_iface: str,
    *,
    ap_ip: str,
    netmask_prefix: int,
    hostapd_conf: str,
) -> str:
    """Create virtual AP with iw (required on this hardware); hostapd attaches to it DOWN."""
    await kill_processes_by_cmdline(hostapd_conf)
    created = await create_virtual_ap(sta_iface, ap_iface)
    if not created:
        raise RuntimeError(
            f"Could not create virtual AP on {sta_iface} "
            f"(tried {ap_interface_candidates(sta_iface, ap_iface)})."
        )
    if not await _interface_exists(created):
        raise RuntimeError(f"Virtual AP {created} missing after iw create")
    await release_wifi_from_network_manager(created, disconnect=False)
    ip_cmd = shutil.which("ip")
    if ip_cmd:
        # No IP yet — hostapd initializes a DOWN __ap interface, then we assign IP.
        await _run_cmd([ip_cmd, "addr", "flush", "dev", created], optional=True)
        await _run_cmd([ip_cmd, "link", "set", created, "down"], optional=True)
    logger.info("Virtual AP %s ready for hostapd (DOWN, no IP yet)", created)
    return created


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
    hw_mode = hw_mode_for_channel(channel)
    ap_name = resolve_ap_interface_name(sta_iface, ap_iface)
    sta_connected = detect_sta_is_connected(sta_iface)
    use_concurrent = concurrent_sta_ap or (
        sta_connected and ap_name and ap_name != sta_iface
    )
    if sta_connected and not concurrent_sta_ap and use_concurrent:
        logger.info(
            "STA connected on %s — auto-using concurrent AP (same channel %d)",
            sta_iface,
            channel,
        )

    if use_concurrent and ap_name and ap_name != sta_iface:
        return ApInterfacePlan(sta_iface, ap_name, "concurrent", channel, hw_mode)

    logger.info("Using dedicated AP on %s", sta_iface)
    await prepare_dedicated_ap(sta_iface)
    await configure_ap_interface_ip(sta_iface, ap_ip, netmask_prefix)
    return ApInterfacePlan(sta_iface, sta_iface, "dedicated", channel, hw_mode)


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
