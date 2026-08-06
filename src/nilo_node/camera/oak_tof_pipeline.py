"""OAK-D-SR ToF + RGB pipeline — DepthAI 2.x (initialConfig) and 3.x (build) compatible."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

TOF_SOCKET_NAME = "CAM_A"


class _TofColormapDefaults:
    phaseUnwrappingLevel = 4


def _tof_socket(dai: Any) -> Any:
    return getattr(dai.CameraBoardSocket, TOF_SOCKET_NAME, dai.CameraBoardSocket.CAM_A)


def _pick_rgb_socket(dai: Any) -> Any:
    for name in ("CAM_C", "CAM_B"):
        socket = getattr(dai.CameraBoardSocket, name, None)
        if socket is not None:
            return socket
    return dai.CameraBoardSocket.CAM_B


def _set_rgb_resolution(cam_rgb: Any, dai: Any) -> None:
    props = dai.ColorCameraProperties.SensorResolution
    for name in ("THE_800_P", "THE_720_P", "THE_1080_P"):
        res = getattr(props, name, None)
        if res is None:
            continue
        try:
            cam_rgb.setResolution(res)
            return
        except Exception:
            continue


def _configure_tof_node_v2(tof: Any) -> Any:
    tof_config = tof.initialConfig.get()
    tof_config.enableOpticalCorrection = True
    tof_config.enablePhaseShuffleTemporalFilter = True
    tof_config.phaseUnwrappingLevel = 4
    tof_config.phaseUnwrapErrorThreshold = 300
    tof_config.enableTemperatureCorrection = False
    tof.initialConfig.set(tof_config)
    return tof_config


def _tof_profile(dai: Any) -> Any | None:
    profile_enum = getattr(getattr(dai, "ToFConfig", None), "Profile", None)
    if profile_enum is None:
        return None
    for name in ("MID_RANGE", "SHORT_RANGE", "LONG_RANGE"):
        value = getattr(profile_enum, name, None)
        if value is not None:
            return value
    return None


def _link_tof_camera_v2(dai: Any, pipeline: Any, tof: Any, *, fps: int) -> None:
    cam_tof = pipeline.create(dai.node.Camera)
    cam_tof.setBoardSocket(_tof_socket(dai))
    cam_tof.setFps(max(fps * 2, 30))
    if hasattr(cam_tof, "setImageOrientation"):
        cam_tof.setImageOrientation(dai.CameraImageOrientation.ROTATE_180_DEG)
    cam_tof.raw.link(tof.input)


def _setup_tof_node_v3_build(dai: Any, tof: Any, *, fps: int) -> None:
    socket = _tof_socket(dai)
    tof_fps = float(max(fps * 2, 30))
    profile = _tof_profile(dai)
    build = tof.build

    if profile is not None:
        try:
            build(boardSocket=socket, profile=profile, fps=tof_fps)
            logger.info("ToF node: build() with profile=%s", profile)
            return
        except TypeError:
            try:
                build(socket, profile, tof_fps)
                logger.info("ToF node: build() positional with profile")
                return
            except Exception as exc:
                logger.warning("ToF build(profile) failed: %s", exc)

    build(boardSocket=socket, fps=tof_fps)
    logger.info("ToF node: build() without profile")


def _add_rgb_stream(dai: Any, pipeline: Any, *, fps: int, width: int = 640, height: int = 480) -> None:
    if not hasattr(dai.node, "ColorCamera"):
        return
    cam_rgb = pipeline.create(dai.node.ColorCamera)
    cam_rgb.setBoardSocket(_pick_rgb_socket(dai))
    _set_rgb_resolution(cam_rgb, dai)
    cam_rgb.setInterleaved(False)
    cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
    cam_rgb.setFps(fps)
    cam_rgb.setPreviewSize(width, height)

    xout_rgb = pipeline.create(dai.node.XLinkOut)
    xout_rgb.setStreamName("rgb")
    cam_rgb.preview.link(xout_rgb.input)


def _add_depth_output(pipeline: Any, tof: Any, dai: Any) -> None:
    xout_depth = pipeline.create(dai.node.XLinkOut)
    xout_depth.setStreamName("depth")
    tof.depth.link(xout_depth.input)


def build_oak_sr_pipeline(
    dai: Any,
    *,
    fps: int = 30,
    include_rgb: bool = True,
    rgb_width: int = 640,
    rgb_height: int = 480,
) -> tuple[Any, Any | None]:
    """Build Luxonis ToF pipeline for OAK-D-SR (CAM_A = ToF sensor)."""
    if not hasattr(dai.node, "ToF"):
        raise RuntimeError(
            "DepthAI SDK missing ToF node. Install depthai>=2.24 for OAK-D-SR (PoE or USB)."
        )

    pipeline = dai.Pipeline()
    tof = pipeline.create(dai.node.ToF)
    tof_config: Any | None = None

    if hasattr(tof, "initialConfig"):
        tof_config = _configure_tof_node_v2(tof)
        if hasattr(dai.node, "Camera"):
            _link_tof_camera_v2(dai, pipeline, tof, fps=fps)
        logger.info("Pipeline: OAK-D-SR ToF (DepthAI initialConfig API)")
    elif hasattr(tof, "build"):
        tof_config = _TofColormapDefaults()
        _setup_tof_node_v3_build(dai, tof, fps=fps)
        logger.info("Pipeline: OAK-D-SR ToF (DepthAI build() API)")
    else:
        tof_config = _TofColormapDefaults()
        if hasattr(tof, "setNumShaves"):
            tof.setNumShaves(1)
        if hasattr(dai.node, "Camera"):
            _link_tof_camera_v2(dai, pipeline, tof, fps=fps)
        logger.info("Pipeline: OAK-D-SR ToF (minimal Camera + ToF link)")

    _add_depth_output(pipeline, tof, dai)

    if include_rgb:
        _add_rgb_stream(dai, pipeline, fps=fps, width=rgb_width, height=rgb_height)

    return pipeline, tof_config


def depth_to_colormap(depth_mm: Any, tof_config: Any | None = None) -> Any:
    import cv2
    import numpy as np

    if depth_mm.size == 0:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    level = 4
    if tof_config is not None:
        level = int(getattr(tof_config, "phaseUnwrappingLevel", 4))
    max_depth = (level + 1) * 1500
    normalized = np.clip(depth_mm.astype(np.float32), 0, max_depth)
    scaled = (normalized / max_depth * 255).astype(np.uint8)
    return cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
