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


def detect_wifi_interface(preferred: str = "") -> str | None:
    """
    Return the best WiFi interface name on this host.

    preferred: explicit name, or "auto"/"" to autodetect.
    """
    pref = (preferred or "").strip().lower()
    if pref and pref not in ("auto", "default"):
        if Path(f"/sys/class/net/{preferred}").exists():
            return preferred
        logger.warning("Configured WiFi interface %s not found — autodetecting", preferred)

    candidates: list[str] = []
    net = Path("/sys/class/net")
    if net.is_dir():
        for entry in sorted(net.iterdir()):
            name = entry.name
            if _is_virtual_iface(name):
                continue
            if _is_wireless_sysfs(name):
                candidates.append(name)

    if not candidates:
        candidates = _wifi_from_iw_dev()

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

    # Several WiFi NICs — pick predictable default (shortest name, e.g. wlan0 vs wlx...)
    chosen = sorted(candidates, key=len)[0]
    logger.info(
        "Multiple WiFi interfaces %s — using %s for AP",
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
