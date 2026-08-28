"""MediaPipe Pose landmarks on host CPU."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class MediapipePoseEngine:
    engine_id = "mediapipe"
    landmark_count = 33

    def __init__(self, model_name: str = "mediapipe") -> None:
        self.model_name = model_name
        self._pose = None
        self._available = False
        try:
            import mediapipe as mp

            if not hasattr(mp, "solutions"):
                raise AttributeError(
                    "mediapipe instalado pero sin API 'solutions' "
                    "(versión nueva — usa pose_backend=yolo o actualiza el engine)"
                )
            self._mp_pose = mp.solutions.pose
            self._pose = self._mp_pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                enable_segmentation=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._available = True
        except ImportError:
            logger.warning("mediapipe not installed — pose engine falls back to stub output")
        except (AttributeError, RuntimeError, OSError) as exc:
            logger.warning("mediapipe pose unavailable (%s) — stub output", exc)

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        if not self._available or self._pose is None:
            return np.zeros((self.landmark_count, 4), dtype=np.float32)

        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        landmarks = np.zeros((self.landmark_count, 4), dtype=np.float32)
        if result.pose_landmarks:
            for idx, lm in enumerate(result.pose_landmarks.landmark[: self.landmark_count]):
                landmarks[idx] = (lm.x, lm.y, lm.z, lm.visibility)
        return landmarks

    def close(self) -> None:
        if self._pose is not None:
            self._pose.close()
            self._pose = None
