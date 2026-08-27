"""Host-side WiFi AP backend (delegates to wifi-ap-run.sh like NiloCardmed)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_host_script_path(configured: str) -> Path:
    install = Path(os.environ.get("NILO_INSTALL_DIR", "/opt/nilo-node"))
    candidates = [
        install / "scripts/wifi/wifi-ap-run.sh",
        Path(configured),
        Path("/host/scripts/wifi/wifi-ap-run.sh"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def resolve_host_script_on_host(configured: str) -> str:
    """Absolute path to wifi-ap-run.sh on the host (for nsenter from container)."""
    install = os.environ.get("NILO_INSTALL_DIR", "/opt/nilo-node")
    return str(Path(install) / "scripts/wifi/wifi-ap-run.sh")


def host_ap_running() -> bool:
    """True if hostapd from wifi-ap-run.sh (wifi-runtime) is running on the host."""
    for pid in _find_pids_by_cmdline("wifi-runtime"):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            continue
    return bool(shutil.which("pgrep") and _pgrep_hostapd())


def _pgrep_hostapd() -> bool:
    try:
        result = shutil.which("pgrep") and __import__("subprocess").run(
            ["pgrep", "-f", "wifi-runtime/hostapd.conf"],
            capture_output=True,
            check=False,
        )
        return bool(result and result.returncode == 0)
    except OSError:
        return False


def _find_pids_by_cmdline(pattern: str) -> list[int]:
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
        if pattern in text and "hostapd" in text:
            pids.append(int(entry.name))
    return pids


async def run_host_wifi_script(script_path: str, action: str) -> tuple[int, str]:
    """Run wifi-ap-run.sh on the host (via nsenter from container, or directly)."""
    install = os.environ.get("NILO_INSTALL_DIR", "/opt/nilo-node")
    host_script = resolve_host_script_on_host(script_path)

    env = os.environ.copy()
    env["NILO_WIFI_ALLOW_HOST_SCRIPTS"] = "1"
    env["NILO_INSTALL_DIR"] = install

    in_docker = Path("/.dockerenv").exists()
    nsenter = shutil.which("nsenter")
    if in_docker and nsenter:
        cmd = [
            nsenter,
            "-t",
            "1",
            "-m",
            "-u",
            "-i",
            "-n",
            "-p",
            "--",
            host_script,
            action,
        ]
        logger.info("Running host WiFi script via nsenter: %s %s", host_script, action)
    elif Path(host_script).is_file():
        cmd = [host_script, action]
    elif Path(script_path).is_file():
        cmd = [script_path, action]
    else:
        raise RuntimeError(f"Host WiFi script not found: {host_script}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode(errors="replace").strip()
    return proc.returncode or 0, text


def resolve_wifi_backend(configured: str, host_script_path: str) -> str:
    if configured in ("container", "host"):
        return configured
    if os.environ.get("NILO_WIFI_BACKEND") == "host":
        return "host"
    if configured == "auto" and resolve_host_script_path(host_script_path).is_file():
        return "host"
    return "container"
