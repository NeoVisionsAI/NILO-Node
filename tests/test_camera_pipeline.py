"""Tests for Phase 7 camera pipeline — FFmpeg writers and pose engines."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nilo_node.camera.ffmpeg_writer import FfmpegRgbWriter, FfmpegTofWriter, ffmpeg_available
from nilo_node.camera.pose.factory import build_pose_engine
from nilo_node.camera.pose.stub_engine import StubPoseEngine
from nilo_node.config.models import CameraConfig


def test_build_pose_engine_mediapipe_without_sdk() -> None:
    cfg = CameraConfig(pose_backend="mediapipe", pose_model="mediapipe")
    engine = build_pose_engine(cfg)
    assert engine.engine_id in ("mediapipe", "stub")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    landmarks = engine.process(frame, 0.0)
    assert landmarks.shape[1] == 4


def test_build_pose_engine_yolo_stub() -> None:
    cfg = CameraConfig(pose_backend="yolo", pose_model="yolo-pose")
    engine = build_pose_engine(cfg)
    assert engine.engine_id == "yolo"
    assert engine.landmark_count == 17


def test_build_pose_engine_custom_requires_plugin() -> None:
    cfg = CameraConfig(pose_backend="custom", pose_plugin="")
    engine = build_pose_engine(cfg)
    assert isinstance(engine, StubPoseEngine)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
def test_ffmpeg_rgb_writer_creates_mp4(tmp_path: Path) -> None:
    writer = FfmpegRgbWriter(tmp_path, width=64, height=48, fps=10)
    frame = np.random.randint(0, 255, (48, 64, 3), dtype=np.uint8)
    for i in range(5):
        writer.write_frame(float(i) * 0.1, frame)
    manifest = writer.finalize()
    assert (tmp_path / "video.mp4").exists()
    assert manifest["mock"] is False
    assert manifest["encoder"] == "ffmpeg"
    assert manifest["frame_count"] == 5


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")
def test_ffmpeg_tof_writer_lossless_mkv(tmp_path: Path) -> None:
    writer = FfmpegTofWriter(tmp_path, width=64, height=48, fps=10, storage_mode="lossless")
    frame = np.random.randint(0, 5000, (48, 64), dtype=np.uint16)
    for i in range(3):
        writer.write_frame(float(i) * 0.1, frame)
    manifest = writer.finalize()
    assert (tmp_path / "depth.mkv").exists()
    assert manifest["codec"] == "ffv1"
    assert manifest["storage_mode"] == "lossless"
