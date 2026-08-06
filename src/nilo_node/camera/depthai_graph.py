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
        self._graph: Any = None
        self._device: Any = None
        self._has_depth = False

    @property
    def is_open(self) -> bool:
        if self._graph is None:
            return False
        if getattr(self._graph, "api", "") == "v3":
            try:
                return self._graph.pipeline.isRunning()
            except Exception:
                return True
        return self._graph.device is not None

    def open(self) -> None:
        import depthai as dai

        from nilo_node.camera.device_connect import resolve_device_info
        from nilo_node.camera.oak_tof_pipeline import open_oak_sr_graph

        info, self._connection_meta = resolve_device_info(
            dai,
            device_id=self._device_id or None,
            device_ip=self._device_ip,
            prefer=self._prefer,
        )
        self._graph = open_oak_sr_graph(
            dai,
            info,
            fps=self._tof_fps,
            include_rgb=True,
            rgb_width=self._rgb_width,
            rgb_height=self._rgb_height,
        )
        self._device = self._graph.device
        self._has_depth = True
        logger.info(
            "DepthAI device session open: %s (%s, %s) depth=True",
            self._connection_meta.get("mxid", self._device_id),
            self._graph.api,
            self._connection_meta.get("connection", "?"),
        )

    def close(self) -> None:
        if self._graph is not None:
            try:
                self._graph.close()
            except Exception as exc:
                logger.debug("DepthAI graph close: %s", exc)
        self._graph = None
        self._device = None

    def poll(self) -> FrameBundle:
        ts = time.time()
        rgb_frame: np.ndarray | None = None
        depth_frame: np.ndarray | None = None

        if self._graph is not None:
            rgb_frame = self._graph.poll_rgb_bgr()
            if rgb_frame is not None:
                if rgb_frame.shape[1] != self._rgb_width or rgb_frame.shape[0] != self._rgb_height:
                    import cv2

                    rgb_frame = cv2.resize(rgb_frame, (self._rgb_width, self._rgb_height))

        if self._graph is not None and self._has_depth:
            depth_raw = self._graph.poll_depth()
            if depth_raw is not None:
                depth_frame = depth_raw
                if depth_frame.shape[1] != self._depth_width or depth_frame.shape[0] != self._depth_height:
                    import cv2

                    depth_frame = cv2.resize(
                        depth_frame,
                        (self._depth_width, self._depth_height),
                        interpolation=cv2.INTER_NEAREST,
                    )

        return FrameBundle(timestamp=ts, rgb=rgb_frame, depth=depth_frame)

