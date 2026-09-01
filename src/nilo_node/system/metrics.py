"""Host system metrics for the setup portal."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UPTIME_PATHS = (
    Path("/host/proc/uptime"),
    Path("/proc/uptime"),
)


def read_system_uptime_sec() -> int:
    """Host uptime in seconds (same basis as the `uptime` command)."""
    for path in _UPTIME_PATHS:
        try:
            raw = path.read_text(encoding="utf-8").strip().split()
            if raw:
                return int(float(raw[0]))
        except (OSError, ValueError, IndexError):
            continue
    return 0


def _collect_sensor_temps(data: Any, readings: list[tuple[float, str]]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("_input") and isinstance(value, (int, float)):
                label = key.replace("_input", "")
                readings.append((float(value), label))
            else:
                _collect_sensor_temps(value, readings)
    elif isinstance(data, list):
        for item in data:
            _collect_sensor_temps(item, readings)


def read_lm_sensors_temperature_c() -> tuple[float | None, str | None]:
    """Temperature from `sensors -j` (lm-sensors), if available."""
    if not shutil.which("sensors"):
        return None, None
    try:
        proc = subprocess.run(
            ["sensors", "-j"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None, None
        payload = json.loads(proc.stdout)
        readings: list[tuple[float, str]] = []
        _collect_sensor_temps(payload, readings)
        if not readings:
            return None, None
        value, label = max(readings, key=lambda item: item[0])
        return value, label
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        logger.debug("lm-sensors unavailable: %s", exc)
        return None, None


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


def read_temperature_c() -> tuple[float | None, str]:
    """Prefer lm-sensors; fall back to sysfs thermal zones."""
    sensors_c, sensors_label = read_lm_sensors_temperature_c()
    if sensors_c is not None:
        pretty = sensors_label.replace("_", " ") if sensors_label else "sensor"
        return sensors_c, f"{sensors_c:.1f} °C ({pretty})"
    thermal_c = read_cpu_temperature_c()
    if thermal_c is not None:
        return thermal_c, f"{thermal_c:.1f} °C"
    return None, "—"


def system_metrics() -> dict[str, Any]:
    uptime_sec = read_system_uptime_sec()
    temp_c, temp_label = read_temperature_c()
    sensors_available = shutil.which("sensors") is not None
    return {
        "uptime_sec": uptime_sec,
        "uptime_human": _format_uptime(uptime_sec),
        "temperature_c": temp_c,
        "temperature_label": temp_label,
        "temperature_source": "lm-sensors" if sensors_available and temp_c is not None else "thermal",
        "sensors_available": sensors_available,
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
    if mins or hours or days:
        parts.append(f"{mins}m")
    elif secs:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"
