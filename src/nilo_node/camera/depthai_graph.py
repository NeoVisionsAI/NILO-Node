"""DepthAI device session — RGB + depth/ToF graph for OAK cameras."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from nilo_node.camera.discovery import depthai_available

logger = logging.getLogger(__name__)


@dataclass
class FrameBundle:
    timestamp: float
    rgb: np.ndarray | None = None
    depth: np.ndarray | None = None


class DepthAiDeviceSession:
    """Wraps DepthAI pipeline lifecycle and non-blocking frame polling."""

    def __init__(
        self,
        device_id: str,
        *,
        device_ip: str = "",
        prefer: str = "auto",
        rgb_fps: int = 30,
        tof_fps: int = 30,
        rgb_width: int = 640,
        rgb_height: int = 480,
        depth_width: int = 640,
        depth_height: int = 480,
    ) -> None:
        if not depthai_available():
            raise RuntimeError("DepthAI SDK not installed")

        self._device_id = device_id
        self._device_ip = device_ip.strip() or None
        self._prefer = prefer
        self._rgb_fps = rgb_fps
        self._tof_fps = tof_fps
        self._rgb_width = rgb_width
        self._rgb_height = rgb_height
        self._depth_width = depth_width
        self._depth_height = depth_height
        self._connection_meta: dict[str, str] = {}
        self._device: Any = None
        self._q_rgb: Any = None
        self._q_depth: Any = None
        self._has_depth = False

    @property
    def is_open(self) -> bool:
        return self._device is not None

    def open(self) -> None:
        import depthai as dai

        from nilo_node.camera.device_connect import resolve_device_info

        pipeline = self._build_pipeline(dai)
        info, self._connection_meta = resolve_device_info(
            dai,
            device_id=self._device_id or None,
            device_ip=self._device_ip,
            prefer=self._prefer,
        )
        self._device = dai.Device(pipeline, info)
        self._q_rgb = self._device.getOutputQueue("rgb", maxSize=4, blocking=False)
        if self._has_depth:
            self._q_depth = self._device.getOutputQueue("depth", maxSize=4, blocking=False)
        logger.info(
            "DepthAI device session open: %s (%s) depth=%s",
            self._connection_meta.get("mxid", self._device_id),
            self._connection_meta.get("connection", "?"),
            self._has_depth,
        )

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception as exc:
                logger.debug("DepthAI device close: %s", exc)
        self._device = None
        self._q_rgb = None
        self._q_depth = None

    def poll(self) -> FrameBundle:
        ts = time.time()
        rgb_frame: np.ndarray | None = None
        depth_frame: np.ndarray | None = None

        if self._q_rgb is not None:
            in_rgb = self._q_rgb.tryGet()
            if in_rgb is not None:
                rgb_frame = in_rgb.getCvFrame()
                if rgb_frame.shape[1] != self._rgb_width or rgb_frame.shape[0] != self._rgb_height:
                    import cv2

                    rgb_frame = cv2.resize(rgb_frame, (self._rgb_width, self._rgb_height))

        if self._q_depth is not None:
            in_depth = self._q_depth.tryGet()
            if in_depth is not None:
                depth_frame = in_depth.getFrame()
                if depth_frame.dtype != np.uint16:
                    depth_frame = depth_frame.astype(np.uint16)
                if depth_frame.shape[1] != self._depth_width or depth_frame.shape[0] != self._depth_height:
                    import cv2

                    depth_frame = cv2.resize(
                        depth_frame,
                        (self._depth_width, self._depth_height),
                        interpolation=cv2.INTER_NEAREST,
                    )

        return FrameBundle(timestamp=ts, rgb=rgb_frame, depth=depth_frame)

    def _build_pipeline(self, dai: Any) -> Any:
        pipeline = dai.Pipeline()
        self._has_depth = False

        cam_rgb = pipeline.create(dai.node.ColorCamera)
        cam_rgb.setBoardSocket(dai.CameraBoardSocket.CAM_A)
        cam_rgb.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        cam_rgb.setInterleaved(False)
        cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam_rgb.setFps(self._rgb_fps)
        cam_rgb.setPreviewSize(self._rgb_width, self._rgb_height)

        xout_rgb = pipeline.create(dai.node.XLinkOut)
        xout_rgb.setStreamName("rgb")
        cam_rgb.preview.link(xout_rgb.input)

        if hasattr(dai.node, "ToF"):
            try:
                tof = pipeline.create(dai.node.ToF)
                tof.setFps(self._tof_fps)
                xout_depth = pipeline.create(dai.node.XLinkOut)
                xout_depth.setStreamName("depth")
                tof.depth.link(xout_depth.input)
                self._has_depth = True
                logger.info("DepthAI pipeline: ToF depth stream enabled")
            except Exception as exc:
                logger.warning("ToF node unavailable (%s) — trying mono fallback", exc)

        if not self._has_depth:
            try:
                mono = pipeline.create(dai.node.MonoCamera)
                mono.setResolution(dai.MonoCameraProperties.SensorResolution.THE_480_P)
                mono.setBoardSocket(dai.CameraBoardSocket.CAM_B)
                mono.setFps(self._tof_fps)

                manip = pipeline.create(dai.node.ImageManip)
                manip.initialConfig.setResize(self._depth_width, self._depth_height)
                manip.initialConfig.setFrameType(dai.ImgFrame.Type.RAW16)
                mono.out.link(manip.input)

                xout_depth = pipeline.create(dai.node.XLinkOut)
                xout_depth.setStreamName("depth")
                manip.out.link(xout_depth.input)
                self._has_depth = True
                logger.info("DepthAI pipeline: mono depth fallback enabled")
            except Exception as exc:
                logger.warning("Depth fallback unavailable: %s — RGB-only capture", exc)

        return pipeline
