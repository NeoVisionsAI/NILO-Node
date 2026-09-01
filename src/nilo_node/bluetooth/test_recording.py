"""Short WAV test clips for Bluetooth mic verification."""

from __future__ import annotations

import asyncio
import logging
import math
import struct
import time
import wave
from pathlib import Path

from nilo_node.bluetooth.models import mac_file_id, normalize_mac

logger = logging.getLogger(__name__)

DEFAULT_TEST_DURATION_SEC = 10
DEFAULT_RETENTION_SEC = 86400


def test_recordings_dir(base_path: Path) -> Path:
    return base_path / "bluetooth-test-recordings"


def cleanup_old_test_recordings(
    directory: Path,
    *,
    max_age_sec: int = DEFAULT_RETENTION_SEC,
) -> int:
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_sec
    removed = 0
    for path in directory.glob("*.wav"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.debug("Could not remove old test recording %s: %s", path, exc)
    return removed


def _write_tone_wav(
    path: Path,
    *,
    duration_sec: float,
    sample_rate: int,
    channels: int,
) -> None:
    frame_count = int(duration_sec * sample_rate)
    frequency = 440.0
    amplitude = 0.25
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for index in range(frame_count):
            sample = amplitude * math.sin(2.0 * math.pi * frequency * index / sample_rate)
            packed = struct.pack("<h", int(sample * 32767))
            if channels > 1:
                wav.writeframes(packed * channels)
            else:
                wav.writeframes(packed)


def wav_waveform_peaks(path: Path, *, buckets: int = 128) -> list[float]:
    """Downsample WAV amplitudes to normalized peaks for UI waveform."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
        raw = wav.readframes(frame_count)
    if not raw or sample_width != 2:
        return [0.0] * buckets
    import struct

    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[: count * 2])
    if channels > 1:
        samples = samples[::channels]
    if not samples:
        return [0.0] * buckets
    chunk = max(1, len(samples) // buckets)
    peaks: list[float] = []
    for i in range(buckets):
        start = i * chunk
        end = min(len(samples), start + chunk)
        if start >= end:
            peaks.append(0.0)
            continue
        peak = max(abs(s) for s in samples[start:end]) / 32768.0
        peaks.append(min(1.0, peak))
    return peaks


async def record_test_wav(
    output_dir: Path,
    mac_address: str,
    *,
    duration_sec: float = DEFAULT_TEST_DURATION_SEC,
    sample_rate: int = 16000,
    channels: int = 1,
) -> tuple[str, Path]:
    """Record a short WAV clip (mock tone today; real BT capture later)."""
    mac = normalize_mac(mac_address)
    output_dir.mkdir(parents=True, exist_ok=True)
    recording_id = f"{mac_file_id(mac)}_{int(time.time())}"
    path = output_dir / f"{recording_id}.wav"

    # Real PulseAudio/PipeWire capture can be wired here; tone proves playback path.
    await asyncio.to_thread(
        _write_tone_wav,
        path,
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        channels=channels,
    )
    logger.info("Bluetooth test recording saved: %s", path)
    return recording_id, path
