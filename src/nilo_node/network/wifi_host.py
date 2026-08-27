"""Host-side WiFi AP backend (delegates to wifi-ap-run.sh like NiloCardmed)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def run_host_wifi_script(script_path: str, action: str) -> tuple[int, str]:
    path = Path(script_path)
    if not path.is_file():
        raise RuntimeError(f"Host WiFi script not found: {script_path}")

    env = os.environ.copy()
    proc = await asyncio.create_subprocess_exec(
        str(path),
        action,
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
    if Path(host_script_path).is_file() and os.environ.get("NILO_WIFI_BACKEND") == "host":
        return "host"
    return "container"
