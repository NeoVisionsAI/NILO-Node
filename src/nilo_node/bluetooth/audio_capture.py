"""Capture audio from PulseAudio/PipeWire (Bluetooth HSP/HFP sources)."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MAC_IN_NAME = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def pulse_available() -> bool:
    return shutil.which("pactl") is not None and shutil.which("parec") is not None


def _normalize_mac_for_match(mac: str) -> str:
    return mac.replace(":", "_").upper()


def find_pulse_source_for_mac(mac_address: str) -> str | None:
    """Return PulseAudio source name for a Bluetooth MAC, if connected."""
    if not pulse_available():
        return None
    mac = mac_address.strip().upper()
    mac_underscore = _normalize_mac_for_match(mac)
    try:
        proc = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("pactl list sources failed: %s", exc)
        return None
    bluez_sources: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        source_name = parts[1]
        upper = source_name.upper()
        if mac_underscore in upper or mac.replace(":", "") in upper.replace(":", ""):
            return source_name
        match = _MAC_IN_NAME.search(source_name)
        if match and match.group(1).upper() == mac:
            return source_name
        if "bluez" in source_name.lower():
            bluez_sources.append(source_name)
    if len(bluez_sources) == 1:
        return bluez_sources[0]
    return None


def _record_with_parec_timed(
    path: Path,
    *,
    duration_sec: float,
    sample_rate: int,
    channels: int,
    source: str | None,
) -> None:
    device = source or "@DEFAULT_SOURCE@"
    cmd = [
        "parec",
        f"--device={device}",
        "--format=s16le",
        f"--rate={sample_rate}",
        f"--channels={channels}",
        "--file-format=wav",
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(max(0.1, duration_sec))
        proc.send_signal(signal.SIGTERM)
        _, stderr = proc.communicate(timeout=8)
        if not path.is_file() or path.stat().st_size <= 44:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "parec produced no audio data")
        if proc.returncode not in (0, -15, None):
            logger.debug("parec exit code %s: %s", proc.returncode, stderr)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("parec did not stop cleanly")


async def record_wav_from_pulse(
    path: Path,
    mac_address: str,
    *,
    duration_sec: float,
    sample_rate: int,
    channels: int,
) -> bool:
    """Record WAV from BT mic via PulseAudio. Returns True if real audio captured."""
    if not pulse_available():
        return False
    source = await asyncio.to_thread(find_pulse_source_for_mac, mac_address)
    if source is None:
        logger.warning("No PulseAudio source found for BT MAC %s", mac_address)
        return False
    await asyncio.to_thread(
        _record_with_parec_timed,
        path,
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        channels=channels,
        source=source,
    )
    return path.is_file() and path.stat().st_size > 44
