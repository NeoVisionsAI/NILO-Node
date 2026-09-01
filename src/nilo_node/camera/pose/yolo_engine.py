"""YOLO pose inference on host CPU (ultralytics) for portal pose-test."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = Path("/data/models/pose/yolo/yolov8n-pose.pt")
YOLO_POSE_DIR = Path("/data/models/pose/yolo/yolo-pose")


class YoloPoseEngine:
    engine_id = "yolo"
    landmark_count = 17

    def __init__(self, model_name: str = "yolo-pose", *, weights_path: Path | None = None) -> None:
        self.model_name = model_name
        self._model = None
        self._available = False
        weights = weights_path or self._resolve_weights()
        if weights is None:
            logger.warning("YOLO weights not found — pose engine returns zero landmarks")
            return
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(weights))
            self._available = True
            logger.info("YOLO pose engine loaded: %s", weights)
        except Exception as exc:
            logger.warning("YOLO pose engine unavailable (%s)", exc)

    @property
    def available(self) -> bool:
        return self._available

    @staticmethod
    def _resolve_weights() -> Path | None:
        candidates = [
            YOLO_POSE_DIR / "yolov8n-pose.pt",
            DEFAULT_WEIGHTS,
        ]
        manifest = YOLO_POSE_DIR / "manifest.json"
        if manifest.is_file():
            try:
                import json

                data = json.loads(manifest.read_text(encoding="utf-8"))
                pt = data.get("weights_pt")
                if pt:
                    candidates.insert(0, Path(pt))
            except (OSError, ValueError):
                pass
        for path in candidates:
            if path.is_file():
                return path
        return None

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        del timestamp
        landmarks = np.zeros((self.landmark_count, 4), dtype=np.float32)
        if not self._available or self._model is None:
            return landmarks
        try:
            results = self._model.predict(frame_bgr, verbose=False, conf=0.35)
            if not results:
                return landmarks
            keypoints = results[0].keypoints
            if keypoints is None or keypoints.data is None or len(keypoints.data) == 0:
                return landmarks
            h, w = frame_bgr.shape[:2]
            kps = keypoints.data[0].cpu().numpy()
            count = min(len(kps), self.landmark_count)
            for idx in range(count):
                x, y, conf = kps[idx][:3]
                landmarks[idx] = (float(x) / w, float(y) / h, 0.0, float(conf))
        except Exception as exc:
            logger.debug("YOLO pose inference failed: %s", exc)
        return landmarks
