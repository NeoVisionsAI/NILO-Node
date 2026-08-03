"""Chunk-level media writers for camera streams."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from nilo_node.config.models import CameraConfig

logger = logging.getLogger(__name__)


class ChunkWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frame_count = 0
        self.timestamps: list[float] = []

    def write_frame(self, timestamp: float, data: object) -> None:
        raise NotImplementedError

    def finalize(self) -> dict:
        raise NotImplementedError

    def _save_timestamps(self, filename: str) -> str:
        path = self.output_dir / filename
        np.save(path, np.array(self.timestamps, dtype=np.float64))
        return filename


class MockRgbWriter(ChunkWriter):
    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        self.frame_count += 1

    def finalize(self) -> dict:
        video_path = self.output_dir / "video.mp4"
        video_path.write_bytes(b"MOCK_MP4")
        ts_file = self._save_timestamps("timestamps.npy")
        return {
            "path": f"sources/rgb/{video_path.name}",
            "timestamps_path": f"sources/rgb/{ts_file}",
            "frame_count": self.frame_count,
            "fps": 30,
            "codec": "h264",
            "mock": True,
        }


class MockTofWriter(ChunkWriter):
    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        self.frame_count += 1

    def finalize(self) -> dict:
        depth_path = self.output_dir / "depth.mkv"
        depth_path.write_bytes(b"MOCK_TOF_MKV")
        ts_file = self._save_timestamps("timestamps.npy")
        return {
            "path": f"sources/tof/{depth_path.name}",
            "timestamps_path": f"sources/tof/{ts_file}",
            "frame_count": self.frame_count,
            "fps": 30,
            "codec": "ffv1",
            "pixel_format": "gray16le",
            "depth_unit": "mm",
            "dtype": "uint16",
            "width": 640,
            "height": 480,
            "mock": True,
        }


class MockPoseWriter(ChunkWriter):
    def __init__(self, output_dir: Path, landmark_count: int = 33) -> None:
        super().__init__(output_dir)
        self.landmark_count = landmark_count
        self._rows: list[np.ndarray] = []

    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        row = np.zeros((self.landmark_count, 4), dtype=np.float32)
        self._rows.append(row)
        self.frame_count += 1

    def finalize(self) -> dict:
        landmarks_path = self.output_dir / "landmarks.npy"
        if self._rows:
            stack = np.stack(self._rows, axis=0)
        else:
            stack = np.zeros((0, self.landmark_count, 4), dtype=np.float32)
        np.save(landmarks_path, stack)
        ts_file = self._save_timestamps("timestamps.npy")
        return {
            "path": f"sources/pose/{landmarks_path.name}",
            "timestamps_path": f"sources/pose/{ts_file}",
            "frame_count": self.frame_count,
            "fps": 15,
            "model": "mediapipe",
            "landmark_count": self.landmark_count,
            "dtype": "float32",
            "shape": list(stack.shape),
            "mock": True,
        }


def create_writer(
    mode: str,
    stream: str,
    output_dir: Path,
    *,
    camera_cfg: CameraConfig | None = None,
) -> ChunkWriter:
    cfg = camera_cfg
    if mode == "depthai" and stream == "rgb":
        from nilo_node.camera.ffmpeg_writer import FfmpegRgbWriter

        fps = cfg.rgb_fps if cfg else 30
        return FfmpegRgbWriter(output_dir, fps=fps)
    if mode == "depthai" and stream == "tof":
        from nilo_node.camera.ffmpeg_writer import FfmpegTofWriter

        fps = cfg.tof_fps if cfg else 30
        storage_mode = cfg.tof_storage_mode if cfg else "lossless"
        return FfmpegTofWriter(output_dir, fps=fps, storage_mode=storage_mode)
    if mode == "mock" and stream == "pose":
        return MockPoseWriter(output_dir)
    if mode == "mock" and stream == "tof":
        return MockTofWriter(output_dir)
    return MockRgbWriter(output_dir)
