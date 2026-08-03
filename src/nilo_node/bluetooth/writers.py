"""Mock and future real audio writers for Bluetooth mic tracks."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from nilo_node.bluetooth.models import mac_file_id, normalize_mac

logger = logging.getLogger(__name__)


class AudioTrackWriter(ABC):
    def __init__(
        self,
        output_dir: Path,
        mac_address: str,
        sample_rate: int,
        channels: int,
    ) -> None:
        self.output_dir = output_dir
        self.mac_address = normalize_mac(mac_address)
        self.sample_rate = sample_rate
        self.channels = channels
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sample_count = 0
        self.timestamps: list[float] = []

    @property
    def file_stem(self) -> str:
        return f"bt_{mac_file_id(self.mac_address)}"

    @abstractmethod
    def write_samples(self, timestamp: float) -> None: ...

    @abstractmethod
    def finalize(self) -> dict: ...

    def _save_timestamps(self) -> str:
        path = self.output_dir / f"{self.file_stem}.timestamps.npy"
        np.save(path, np.array(self.timestamps, dtype=np.float64))
        return f"sources/audio/{path.name}"


class MockAudioTrackWriter(AudioTrackWriter):
    def write_samples(self, timestamp: float) -> None:
        self.timestamps.append(timestamp)
        self.sample_count += self.sample_rate // 10

    def finalize(self) -> dict:
        audio_path = self.output_dir / f"{self.file_stem}.flac"
        audio_path.write_bytes(b"MOCK_FLAC")
        ts_path = self._save_timestamps()
        return {
            "mic_id": f"bt:{self.mac_address}",
            "path": f"sources/audio/{audio_path.name}",
            "timestamps_path": ts_path,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_count": self.sample_count,
            "mock": True,
        }


def create_audio_writer(
    mode: str,
    output_dir: Path,
    mac_address: str,
    sample_rate: int,
    channels: int,
) -> AudioTrackWriter:
    if mode == "mock":
        return MockAudioTrackWriter(output_dir, mac_address, sample_rate, channels)
    return MockAudioTrackWriter(output_dir, mac_address, sample_rate, channels)
