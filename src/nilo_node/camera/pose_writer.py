"""Pose landmark accumulation writer."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from nilo_node.camera.writers import ChunkWriter


class PoseLandmarkWriter(ChunkWriter):
    def __init__(
        self,
        output_dir: Path,
        *,
        engine_id: str,
        landmark_count: int = 33,
        fps: int = 15,
    ) -> None:
        super().__init__(output_dir)
        self._engine_id = engine_id
        self._landmark_count = landmark_count
        self._fps = fps
        self._rows: list[np.ndarray] = []

    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        if isinstance(data, np.ndarray) and data.ndim == 2:
            self._rows.append(data.astype(np.float32, copy=False))
        else:
            self._rows.append(np.zeros((self._landmark_count, 4), dtype=np.float32))
        self.frame_count += 1

    def finalize(self) -> dict:
        landmarks_path = self.output_dir / "landmarks.npy"
        if self._rows:
            stack = np.stack(self._rows, axis=0)
        else:
            stack = np.zeros((0, self._landmark_count, 4), dtype=np.float32)
        np.save(landmarks_path, stack)
        ts_file = self._save_timestamps("timestamps.npy")
        return {
            "path": f"sources/pose/{landmarks_path.name}",
            "timestamps_path": f"sources/pose/{ts_file}",
            "frame_count": self.frame_count,
            "fps": self._fps,
            "model": self._engine_id,
            "landmark_count": self._landmark_count,
            "dtype": "float32",
            "shape": list(stack.shape),
            "mock": False,
        }
