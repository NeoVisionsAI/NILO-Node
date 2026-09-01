"""MediaPipe Pose landmarks on host CPU (solutions or Tasks API)."""

from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
)

# MediaPipe BlazePose 33-landmark topology.
POSE_CONNECTIONS = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 7),
    (0, 4),
    (4, 5),
    (5, 6),
    (6, 8),
    (9, 10),
    (11, 12),
    (11, 13),
    (13, 15),
    (15, 17),
    (15, 19),
    (15, 21),
    (17, 19),
    (12, 14),
    (14, 16),
    (16, 18),
    (16, 20),
    (16, 22),
    (18, 20),
    (11, 23),
    (12, 24),
    (23, 24),
    (23, 25),
    (24, 26),
    (25, 27),
    (26, 28),
    (27, 29),
    (28, 30),
    (29, 31),
    (30, 32),
    (27, 31),
    (28, 32),
)


def _model_cache_dir() -> Path:
    for raw in (
        os.environ.get("NILO_MODEL_CACHE"),
        "/data/models",
        str(Path.home() / ".cache" / "nilo-node" / "models"),
    ):
        if not raw:
            continue
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    fallback = Path("/tmp/nilo-node-models")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def ensure_pose_landmarker_model() -> Path:
    dest = _model_cache_dir() / "pose_landmarker_lite.task"
    if dest.is_file() and dest.stat().st_size > 1024:
        return dest
    logger.info("Downloading MediaPipe pose model to %s", dest)
    tmp = dest.with_suffix(".task.part")
    urllib.request.urlretrieve(POSE_MODEL_URL, tmp)
    tmp.replace(dest)
    return dest


def draw_pose_landmarks(frame_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    import cv2

    h, w = frame_bgr.shape[:2]
    out = frame_bgr.copy()
    points: list[tuple[int, int] | None] = []
    for row in landmarks:
        x, y, _, vis = row
        if vis > 0.5:
            points.append((int(x * w), int(y * h)))
        else:
            points.append(None)
    for start, end in POSE_CONNECTIONS:
        if start >= len(points) or end >= len(points):
            continue
        p0, p1 = points[start], points[end]
        if p0 and p1:
            cv2.line(out, p0, p1, (80, 220, 100), 2, cv2.LINE_AA)
    for point in points:
        if point:
            cv2.circle(out, point, 4, (60, 120, 255), -1, lineType=cv2.LINE_AA)
    return out


class MediapipePoseEngine:
    engine_id = "mediapipe"
    landmark_count = 33

    def __init__(self, model_name: str = "mediapipe") -> None:
        self.model_name = model_name
        self._backend: str | None = None
        self._pose = None
        self._landmarker = None
        self._available = False
        self._init_error: str | None = None
        if self._init_solutions() or self._init_tasks():
            return
        logger.warning("mediapipe pose unavailable — stub output")

    @property
    def available(self) -> bool:
        return self._available

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def _init_solutions(self) -> bool:
        try:
            import mediapipe as mp

            if not hasattr(mp, "solutions"):
                return False
            self._mp_pose = mp.solutions.pose
            self._pose = self._mp_pose.Pose(
                static_image_mode=False,
                model_complexity=1,
                enable_segmentation=False,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            self._backend = "solutions"
            self._available = True
            return True
        except ImportError:
            logger.warning("mediapipe not installed — pose engine falls back to stub output")
            self._init_error = "mediapipe no instalado"
        except (AttributeError, RuntimeError, OSError) as exc:
            logger.debug("mediapipe solutions API unavailable: %s", exc)
            self._init_error = str(exc)
        return False

    def _init_tasks(self) -> bool:
        try:
            from mediapipe.tasks import python as mp_tasks
            from mediapipe.tasks.python import vision

            model_path = str(ensure_pose_landmarker_model())
            options = vision.PoseLandmarkerOptions(
                base_options=mp_tasks.BaseOptions(model_asset_path=model_path),
                running_mode=vision.RunningMode.IMAGE,
                min_pose_detection_confidence=0.5,
                min_pose_presence_confidence=0.5,
            )
            self._landmarker = vision.PoseLandmarker.create_from_options(options)
            self._backend = "tasks"
            self._available = True
            logger.info("MediaPipe pose using Tasks API (%s)", model_path)
            return True
        except ImportError:
            self._init_error = "mediapipe tasks API no instalada"
            return False
        except (AttributeError, RuntimeError, OSError, ValueError) as exc:
            logger.warning("mediapipe tasks API unavailable (%s)", exc)
            self._init_error = str(exc)
            return False

    def process(self, frame_bgr: np.ndarray, timestamp: float) -> np.ndarray:
        del timestamp  # unused; kept for PoseEngine protocol
        if not self._available:
            return np.zeros((self.landmark_count, 4), dtype=np.float32)
        if self._backend == "tasks":
            return self._process_tasks(frame_bgr)
        return self._process_solutions(frame_bgr)

    def _process_solutions(self, frame_bgr: np.ndarray) -> np.ndarray:
        import cv2

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        landmarks = np.zeros((self.landmark_count, 4), dtype=np.float32)
        if result.pose_landmarks:
            for idx, lm in enumerate(result.pose_landmarks.landmark[: self.landmark_count]):
                landmarks[idx] = (lm.x, lm.y, lm.z, lm.visibility)
        return landmarks

    def _process_tasks(self, frame_bgr: np.ndarray) -> np.ndarray:
        import cv2
        import mediapipe as mp

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)
        landmarks = np.zeros((self.landmark_count, 4), dtype=np.float32)
        if result.pose_landmarks:
            pose = result.pose_landmarks[0]
            for idx, lm in enumerate(pose[: self.landmark_count]):
                visibility = float(getattr(lm, "visibility", 1.0) or 1.0)
                landmarks[idx] = (lm.x, lm.y, lm.z, visibility)
        return landmarks

    def close(self) -> None:
        if self._pose is not None:
            self._pose.close()
            self._pose = None
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
        self._available = False
        self._backend = None
