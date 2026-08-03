"""Pose engine protocol for pluggable body landmark extraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class PoseEngine(Protocol):
    engine_id: str
    landmark_count: int

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        """Return landmarks array shape (landmark_count, 4) — x, y, z, visibility."""
        ...
