"""OAK-D-SR ToF + RGB pipeline builders for hardware test scripts."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import numpy as np

from nilo_node.camera.oak_tof_pipeline import build_oak_sr_pipeline, depth_to_colormap
from oak.device_connect import list_devices, resolve_device_info

__all__ = ["OakFrameSet", "OakSrSession", "build_oak_sr_pipeline", "depth_to_colormap", "list_devices"]

logger = logging.getLogger(__name__)


@dataclass
class OakFrameSet:
    rgb: np.ndarray | None = None
    depth_mm: np.ndarray | None = None
    depth_colormap: np.ndarray | None = None


class OakSrSession:
    """Blocking DepthAI session for GUI test tools (PoE or USB)."""

    def __init__(
        self,
        device_id: str | None = None,
        *,
        device_ip: str | None = None,
        fps: int = 30,
        prefer: str | None = None,
    ) -> None:
        self._device_id = device_id
        self._device_ip = device_ip
        self._fps = fps
        self._prefer = prefer or os.environ.get("OAK_CONNECTION", "auto")
        self._connection_meta: dict[str, str] = {}
        self._device: Any = None
        self._q_depth: Any = None
        self._q_rgb: Any = None
        self._tof_config: Any | None = None
        self._latest_depth: np.ndarray | None = None
        self._latest_rgb: np.ndarray | None = None

    @property
    def connected(self) -> bool:
        return self._device is not None

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def latest_depth(self) -> np.ndarray | None:
        return self._latest_depth

    @property
    def connection_meta(self) -> dict[str, str]:
        return dict(self._connection_meta)

    def connect(self) -> list[dict[str, str]]:
        import depthai as dai

        available: list[dict[str, str]] = []
        try:
            available = list_devices()
        except Exception as exc:
            logger.warning("Device list failed (%s) — will try direct IP if configured", exc)

        pipeline, self._tof_config = build_oak_sr_pipeline(dai, fps=self._fps, include_rgb=True)
        info, self._connection_meta = resolve_device_info(
            dai,
            device_id=self._device_id,
            device_ip=self._device_ip,
            prefer=self._prefer,
        )
        self._device = dai.Device(pipeline, info)
        self._q_depth = self._device.getOutputQueue("depth", maxSize=4, blocking=False)
        try:
            self._q_rgb = self._device.getOutputQueue("rgb", maxSize=4, blocking=False)
        except Exception:
            self._q_rgb = None

        self._device_id = self._connection_meta.get("mxid") or self._device_id
        features = self._device.getConnectedCameraFeatures()
        logger.info(
            "Connected OAK (%s) %s — cameras: %s",
            self._connection_meta.get("connection", "?"),
            self._device_id,
            features,
        )
        return available or [self._connection_meta]

    def disconnect(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
        self._device = None
        self._q_depth = None
        self._q_rgb = None
        self._latest_depth = None
        self._latest_rgb = None

    def poll(self) -> OakFrameSet:
        if self._q_depth is not None:
            frame = self._q_depth.tryGet()
            if frame is not None:
                depth = frame.getFrame()
                if depth.dtype != np.uint16:
                    depth = depth.astype(np.uint16)
                self._latest_depth = depth

        if self._q_rgb is not None:
            frame = self._q_rgb.tryGet()
            if frame is not None:
                self._latest_rgb = frame.getCvFrame()

        depth = self._latest_depth
        colormap = depth_to_colormap(depth, self._tof_config) if depth is not None else None
        return OakFrameSet(rgb=self._latest_rgb, depth_mm=depth, depth_colormap=colormap)

    def depth_at(self, x: int, y: int) -> int | None:
        if self._latest_depth is None:
            return None
        h, w = self._latest_depth.shape[:2]
        x = max(0, min(w - 1, x))
        y = max(0, min(h - 1, y))
        value = int(self._latest_depth[y, x])
        return value if value > 0 else None

    def depth_center_mm(self) -> int | None:
        if self._latest_depth is None:
            return None
        h, w = self._latest_depth.shape[:2]
        return self.depth_at(w // 2, h // 2)
