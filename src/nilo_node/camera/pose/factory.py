"""Build pose engine from camera config."""

from __future__ import annotations

import importlib
import logging

from nilo_node.camera.pose.base import PoseEngine
from nilo_node.camera.pose.mediapipe_engine import MediapipePoseEngine
from nilo_node.camera.pose.stub_engine import StubPoseEngine
from nilo_node.camera.pose.yolo_engine import YoloPoseEngine
from nilo_node.config.models import CameraConfig

logger = logging.getLogger(__name__)


def build_pose_engine(config: CameraConfig) -> PoseEngine:
    backend = config.pose_backend
    model = config.pose_model

    if backend == "mediapipe":
        return MediapipePoseEngine(model)
    if backend == "yolo":
        return YoloPoseEngine(model)
    if backend == "custom":
        plugin = config.pose_plugin.strip()
        if not plugin:
            logger.warning("pose_backend=custom but pose_plugin empty — using stub")
            return StubPoseEngine(model)
        return _load_custom_engine(plugin, model)

    logger.warning("Unknown pose_backend=%s — using stub", backend)
    return StubPoseEngine(model)


def _load_custom_engine(plugin_path: str, model_name: str) -> PoseEngine:
    module_path, _, class_name = plugin_path.rpartition(".")
    if not module_path or not class_name:
        raise ValueError(f"Invalid pose_plugin path: {plugin_path}")

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    engine = cls(model_name)
    if not isinstance(engine, PoseEngine):
        raise TypeError(f"{plugin_path} does not implement PoseEngine")
    return engine
