"""YOLO pose backend placeholder — swap model when benchmarked on hardware."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class YoloPoseEngine:
    engine_id = "yolo"
    landmark_count = 17

    def __init__(self, model_name: str = "yolo-pose") -> None:
        self.model_name = model_name
        logger.info(
            "YOLO pose engine stub active (model=%s) — returns zero landmarks until model wired",
            model_name,
        )

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        return np.zeros((self.landmark_count, 4), dtype=np.float32)
