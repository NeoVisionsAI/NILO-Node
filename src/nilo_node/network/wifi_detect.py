"""Detect the host WiFi interface for AP mode."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr", "lo")


def _is_virtual_iface(name: str) -> bool:
    if name == "lo":
        return True
    return any(name.startswith(p) for p in _VIRTUAL_PREFIXES if p != "lo")


def _is_wireless_sysfs(iface: str) -> bool:
    base = Path(f"/sys/class/net/{iface}")
    if not base.is_dir():
        return False
    if (base / "wireless").exists():
        return True
    uevent = (base / "uevent").read_text(encoding="utf-8", errors="ignore")
    return "DEVTYPE=wlan" in uevent


def _wifi_from_iw_dev() -> list[str]:
    try:
        result = subprocess.run(
            ["iw", "dev"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    names: list[str] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Interface "):
            names.append(stripped.split()[-1])
    return names


def _iw_interface_type(iface: str) -> str | None:
    try:
        result = subprocess.run(
            ["iw", "dev", iface, "info"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "type":
            return parts[1]
    return None


def _is_virtual_ap_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("uap") or lowered.endswith("-ap") or lowered == "ap0"


def detect_wifi_interface(
    preferred: str = "",
    *,
    exclude: frozenset[str] | None = None,
) -> str | None:
    """
    Return the physical WiFi STA interface on this host (never a virtual AP like uap0).

    preferred: explicit name, or "auto"/"" to autodetect.
    exclude: extra names to skip (e.g. configured ap_interface).
    """
    skip = exclude or frozenset()
    pref = (preferred or "").strip().lower()
    if pref and pref not in ("auto", "default"):
        if (
            Path(f"/sys/class/net/{preferred}").exists()
            and preferred not in skip
            and not _is_virtual_ap_name(preferred)
            and _iw_interface_type(preferred) not in ("AP", "__ap")
        ):
            return preferred
        logger.warning("Configured WiFi interface %s not found — autodetecting", preferred)

    candidates: list[str] = []
    net = Path("/sys/class/net")
    if net.is_dir():
        for entry in sorted(net.iterdir()):
            name = entry.name
            if _is_virtual_iface(name):
                continue
            if name in skip or _is_virtual_ap_name(name):
                continue
            if _is_wireless_sysfs(name):
                candidates.append(name)

    if not candidates:
        for name in _wifi_from_iw_dev():
            if name in skip or _is_virtual_ap_name(name):
                continue
            if _iw_interface_type(name) in ("AP", "__ap"):
                continue
            candidates.append(name)

    # De-duplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    candidates = unique

    if not candidates:
        return None
    if len(candidates) == 1:
        logger.info("Auto-detected WiFi interface: %s", candidates[0])
        return candidates[0]

    # Prefer managed/station NICs; never pick a leftover virtual AP.
    managed = [
        n
        for n in candidates
        if _iw_interface_type(n) in (None, "managed", "station")
    ]
    pool = managed or candidates
    chosen = sorted(pool, key=len)[0]
    logger.info(
        "Multiple WiFi interfaces %s — using %s for STA",
        candidates,
        chosen,
    )
    return chosen


def resolve_wifi_interface(configured: str) -> str:
    """Like detect_wifi_interface but falls back to configured literal or wlan0."""
    found = detect_wifi_interface(configured)
    if found:
        return found
    if configured and configured.lower() not in ("auto", "default"):
        return configured
    return "wlan0"
