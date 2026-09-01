"""Zero-filled landmarks when pose backend is unavailable."""

from __future__ import annotations

import numpy as np


class StubPoseEngine:
    engine_id = "stub"
    landmark_count = 33
    available = False

    def __init__(self, model_name: str = "stub") -> None:
        self.model_name = model_name

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        return np.zeros((self.landmark_count, 4), dtype=np.float32)
