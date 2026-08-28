"""Capture pipeline — mock and DepthAI backends."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from nilo_node.camera.discovery import depthai_available
from nilo_node.camera.models import CaptureFlags
from nilo_node.camera.pose.factory import build_pose_engine
from nilo_node.camera.pose_writer import PoseLandmarkWriter
from nilo_node.camera.writers import ChunkWriter, create_writer
from nilo_node.config.models import CameraConfig

if TYPE_CHECKING:
    from nilo_node.camera.depthai_graph import DepthAiDeviceSession

logger = logging.getLogger(__name__)


@dataclass
class ChunkCaptureSession:
    chunk_id: str
    chunk_path: Path
    flags: CaptureFlags
    writers: dict[str, ChunkWriter] = field(default_factory=dict)
    task: asyncio.Task | None = None
    running: bool = False


class CapturePipeline(ABC):
    mode: str

    @abstractmethod
    async def start(self, device_id: str | None) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def begin_chunk(self, session: ChunkCaptureSession) -> None: ...

    @abstractmethod
    async def end_chunk(self, session: ChunkCaptureSession) -> dict[str, dict]: ...

    def is_alive(self) -> bool:
        return True


class MockCapturePipeline(CapturePipeline):
    mode = "mock"

    def __init__(self, camera_cfg: CameraConfig) -> None:
        self._cfg = camera_cfg
        self._connected = False
        self._device_id: str | None = None

    async def start(self, device_id: str | None) -> None:
        self._connected = True
        self._device_id = device_id or "mock-oak-device"

    async def stop(self) -> None:
        self._connected = False
        self._device_id = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def begin_chunk(self, session: ChunkCaptureSession) -> None:
        flags = session.flags
        base = session.chunk_path / "sources"
        session.writers = {}
        if flags.rgb:
            session.writers["rgb"] = create_writer("mock", "rgb", base / "rgb", camera_cfg=self._cfg)
        if flags.tof:
            session.writers["tof"] = create_writer("mock", "tof", base / "tof", camera_cfg=self._cfg)
        if flags.pose:
            session.writers["pose"] = create_writer("mock", "pose", base / "pose", camera_cfg=self._cfg)
        session.running = True
        session.task = asyncio.create_task(self._capture_loop(session))

    async def _capture_loop(self, session: ChunkCaptureSession) -> None:
        start = time.time()
        rgb_interval = 1.0 / max(self._cfg.rgb_fps, 1)
        tof_interval = 1.0 / max(self._cfg.tof_fps, 1)
        pose_interval = 1.0 / max(self._cfg.pose_fps, 1)
        next_rgb = next_tof = next_pose = start

        while session.running:
            now = time.time()
            ts = time.time()
            if session.flags.rgb and now >= next_rgb and "rgb" in session.writers:
                session.writers["rgb"].write_frame(ts, None)
                next_rgb += rgb_interval
            if session.flags.tof and now >= next_tof and "tof" in session.writers:
                session.writers["tof"].write_frame(ts, None)
                next_tof += tof_interval
            if session.flags.pose and now >= next_pose and "pose" in session.writers:
                session.writers["pose"].write_frame(ts, None)
                next_pose += pose_interval
            await asyncio.sleep(0.01)

    async def end_chunk(self, session: ChunkCaptureSession) -> dict[str, dict]:
        session.running = False
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        manifests: dict[str, dict] = {}
        for name, writer in session.writers.items():
            manifests[name] = writer.finalize()
        session.writers.clear()
        return manifests


class DepthAiCapturePipeline(CapturePipeline):
    """DepthAI hardware capture with FFmpeg encoders and pluggable pose engine."""

    mode = "depthai"

    def __init__(self, camera_cfg: CameraConfig) -> None:
        self._cfg = camera_cfg
        self._device_id: str | None = None
        self._session: Any = None
        self._pose_engine: Any = None
        self._use_synthetic = False

    def _get_pose_engine(self):
        if self._pose_engine is None:
            self._pose_engine = build_pose_engine(self._cfg)
        return self._pose_engine

    async def start(self, device_id: str | None) -> None:
        from nilo_node.camera.discovery import discover_devices
        from nilo_node.camera.depthai_graph import DepthAiDeviceSession

        if not depthai_available():
            raise RuntimeError("DepthAI SDK not installed")

        available = discover_devices()
        prefer = self._cfg.connection_mode
        device_ip = self._cfg.device_ip.strip() or None

        if not available and not device_ip:
            raise RuntimeError(
                "No OAK camera found (USB or PoE). "
                "PoE: configure host Ethernet — see docs/POE_SETUP.md"
            )

        if device_id:
            if available and not any(d.device_id == device_id for d in available):
                if not device_ip:
                    raise RuntimeError(f"Camera {device_id} not found")
            self._device_id = device_id
        elif available:
            if prefer == "poe":
                poe = [d for d in available if d.state == "poe"]
                self._device_id = (poe[0] if poe else available[0]).device_id
            elif prefer == "usb":
                usb = [d for d in available if d.state == "usb"]
                self._device_id = (usb[0] if usb else available[0]).device_id
            else:
                self._device_id = available[0].device_id
        else:
            self._device_id = device_ip or "poe-oak"

        self._session = DepthAiDeviceSession(
            self._device_id,
            device_ip=device_ip or "",
            prefer=prefer,
            rgb_fps=self._cfg.rgb_fps,
            tof_fps=self._cfg.tof_fps,
        )
        try:
            await asyncio.to_thread(self._session.open)
            self._use_synthetic = False
        except Exception as exc:
            logger.warning(
                "DepthAI graph failed (%s) — using synthetic frames with FFmpeg encoders",
                exc,
            )
            self._session = None
            self._use_synthetic = True

        logger.info(
            "DepthAI pipeline ready device=%s synthetic=%s pose=%s",
            self._device_id,
            self._use_synthetic,
            self._cfg.pose_backend,
        )

    async def stop(self) -> None:
        if self._session is not None:
            await asyncio.to_thread(self._session.close)
            self._session = None
        if self._pose_engine is not None and hasattr(self._pose_engine, "close"):
            self._pose_engine.close()  # type: ignore[union-attr]
        self._pose_engine = None
        self._device_id = None

    def is_alive(self) -> bool:
        if self._use_synthetic:
            return self._device_id is not None
        if self._session is None:
            return False
        if not self._session.is_open:
            return False
        graph = getattr(self._session, "_graph", None)
        if graph is not None and getattr(graph, "api", "") == "v3":
            try:
                return graph.pipeline.isRunning()
            except Exception:
                return True
        return True

    async def begin_chunk(self, session: ChunkCaptureSession) -> None:
        flags = session.flags
        base = session.chunk_path / "sources"
        session.writers = {}
        if flags.rgb:
            session.writers["rgb"] = create_writer("depthai", "rgb", base / "rgb", camera_cfg=self._cfg)
        if flags.tof:
            session.writers["tof"] = create_writer("depthai", "tof", base / "tof", camera_cfg=self._cfg)
        if flags.pose:
            pose_engine = self._get_pose_engine()
            session.writers["pose"] = PoseLandmarkWriter(
                base / "pose",
                engine_id=pose_engine.engine_id,
                landmark_count=pose_engine.landmark_count,
                fps=self._cfg.pose_fps,
            )
        session.running = True
        session.task = asyncio.create_task(self._capture_loop(session))

    async def _capture_loop(self, session: ChunkCaptureSession) -> None:
        rgb_interval = 1.0 / max(self._cfg.rgb_fps, 1)
        tof_interval = 1.0 / max(self._cfg.tof_fps, 1)
        pose_interval = 1.0 / max(self._cfg.pose_fps, 1)
        next_rgb = next_tof = next_pose = time.time()
        h, w = 480, 640

        while session.running:
            now = time.time()
            bundle = None
            if self._session is not None and not self._use_synthetic:
                bundle = await asyncio.to_thread(self._session.poll)

            ts = bundle.timestamp if bundle else time.time()
            rgb_frame = bundle.rgb if bundle else None
            depth_frame = bundle.depth if bundle else None

            if rgb_frame is None and (session.flags.rgb or session.flags.pose):
                rgb_frame = np.zeros((h, w, 3), dtype=np.uint8)

            if depth_frame is None and session.flags.tof:
                depth_frame = np.zeros((h, w), dtype=np.uint16)

            if session.flags.rgb and now >= next_rgb and "rgb" in session.writers and rgb_frame is not None:
                session.writers["rgb"].write_frame(ts, rgb_frame)
                next_rgb += rgb_interval

            if session.flags.tof and now >= next_tof and "tof" in session.writers and depth_frame is not None:
                session.writers["tof"].write_frame(ts, depth_frame)
                next_tof += tof_interval

            if session.flags.pose and now >= next_pose and "pose" in session.writers and rgb_frame is not None:
                landmarks = self._get_pose_engine().process(rgb_frame, ts)
                session.writers["pose"].write_frame(ts, landmarks)
                next_pose += pose_interval

            await asyncio.sleep(0.001)

    async def end_chunk(self, session: ChunkCaptureSession) -> dict[str, dict]:
        session.running = False
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        manifests: dict[str, dict] = {}
        for name, writer in session.writers.items():
            manifests[name] = writer.finalize()
        session.writers.clear()
        return manifests


def build_pipeline(use_depthai: bool, camera_cfg: CameraConfig) -> CapturePipeline:
    if use_depthai:
        return DepthAiCapturePipeline(camera_cfg)
    return MockCapturePipeline(camera_cfg)
