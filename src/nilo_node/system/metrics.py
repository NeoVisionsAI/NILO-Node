"""Host system metrics for the setup portal."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BOOT_TIME = time.time()


def read_cpu_temperature_c() -> float | None:
    """Best-effort CPU/thermal zone temperature in °C."""
    thermal_root = Path("/sys/class/thermal")
    if not thermal_root.is_dir():
        return None
    readings: list[float] = []
    for zone in sorted(thermal_root.glob("thermal_zone*")):
        temp_file = zone / "temp"
        if not temp_file.is_file():
            continue
        try:
            milli = int(temp_file.read_text(encoding="utf-8").strip())
            readings.append(milli / 1000.0)
        except (OSError, ValueError):
            continue
    if not readings:
        return None
    return max(readings)


def system_metrics() -> dict[str, Any]:
    uptime_sec = int(time.time() - _BOOT_TIME)
    temp_c = read_cpu_temperature_c()
    return {
        "uptime_sec": uptime_sec,
        "uptime_human": _format_uptime(uptime_sec),
        "temperature_c": temp_c,
        "temperature_label": f"{temp_c:.1f} °C" if temp_c is not None else "—",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _format_uptime(seconds: int) -> str:
    days, rem = divmod(max(0, seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    if days == 0 and hours == 0:
        parts.append(f"{secs}s")
    return " ".join(parts)
