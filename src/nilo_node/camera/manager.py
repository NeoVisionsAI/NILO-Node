"""Central camera connection and chunk capture orchestration."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nilo_node.camera.device_connect import should_use_depthai_hardware
from nilo_node.camera.discovery import depthai_available, discover_devices
from nilo_node.camera.models import (
    CameraConnectionState,
    CameraDeviceInfo,
    CameraStatus,
    CaptureFlags,
)
from nilo_node.camera.pipeline import ChunkCaptureSession, build_pipeline
from nilo_node.config.models import AppConfig, CameraConfig
from nilo_node.monitoring.models import Campaign
from nilo_node.sources.base import SourceManifest

logger = logging.getLogger(__name__)


class CameraManager:
    """Singleton-style service: discover, connect, capture with per-campaign flags."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._camera_cfg: CameraConfig = config.camera
        self._lock = asyncio.Lock()
        self._pipeline = build_pipeline(use_depthai=False, camera_cfg=self._camera_cfg)
        self._state = CameraConnectionState.DISCONNECTED
        self._connected_device_id: str | None = None
        self._last_error: str | None = None
        self._active_campaign: Campaign | None = None
        self._capture_flags = CaptureFlags(
            rgb=self._camera_cfg.defaults.rgb_enabled,
            tof=self._camera_cfg.defaults.tof_enabled,
            pose=self._camera_cfg.defaults.pose_enabled,
        )
        self._sessions: dict[str, ChunkCaptureSession] = {}
        self._session_manifests: dict[str, dict[str, dict]] = {}
        self._recording = False
        self._watchdog_task: asyncio.Task[None] | None = None

    def set_campaign(self, campaign: Campaign | None) -> None:
        self._active_campaign = campaign
        if campaign is not None:
            self._capture_flags = CaptureFlags.from_campaign_sources(
                campaign.sources,
                defaults=CaptureFlags(
                    rgb=self._camera_cfg.defaults.rgb_enabled,
                    tof=self._camera_cfg.defaults.tof_enabled,
                    pose=self._camera_cfg.defaults.pose_enabled,
                ),
            )
            logger.info(
                "Capture flags from backend campaign: rgb=%s tof=%s pose=%s",
                self._capture_flags.rgb,
                self._capture_flags.tof,
                self._capture_flags.pose,
            )

    @property
    def capture_flags(self) -> CaptureFlags:
        return self._capture_flags

    async def discover(self) -> list[CameraDeviceInfo]:
        return discover_devices()

    def _build_pipeline_for_connect(self, use_depthai: bool):
        return build_pipeline(use_depthai=use_depthai, camera_cfg=self._camera_cfg)

    def apply_config(self, camera_cfg: CameraConfig) -> None:
        """Apply updated camera settings (e.g. from setup API)."""
        self._camera_cfg = camera_cfg
        self._capture_flags = CaptureFlags(
            rgb=camera_cfg.defaults.rgb_enabled,
            tof=camera_cfg.defaults.tof_enabled,
            pose=camera_cfg.defaults.pose_enabled,
        )

    async def get_preview_jpeg(self) -> bytes | None:
        from nilo_node.camera.models import CameraConnectionState

        if self._state != CameraConnectionState.CONNECTED:
            return None

        pipeline = self._pipeline
        if hasattr(pipeline, "_session") and pipeline._session is not None and not getattr(
            pipeline, "_use_synthetic", True
        ):
            bundle = await asyncio.to_thread(pipeline._session.poll)
            if bundle is not None and bundle.rgb is not None:
                try:
                    import cv2

                    ok, jpeg = cv2.imencode(".jpg", bundle.rgb, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    if ok:
                        return jpeg.tobytes()
                except ImportError:
                    pass

        if self._pipeline.mode == "mock":
            try:
                import cv2
                import numpy as np

                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(
                    frame,
                    "Mock camera preview",
                    (40, 240),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (200, 200, 200),
                    2,
                )
                ok, jpeg = cv2.imencode(".jpg", frame)
                if ok:
                    return jpeg.tobytes()
            except ImportError:
                return None
        return None

    def _use_depthai(self, devices: list[CameraDeviceInfo]) -> bool:
        return should_use_depthai_hardware(
            depthai_ok=depthai_available(),
            devices=devices,
            device_ip=self._camera_cfg.device_ip,
            connection_mode=self._camera_cfg.connection_mode,
        )

    async def connect(self, device_id: str | None = None) -> CameraStatus:
        async with self._lock:
            self._state = CameraConnectionState.CONNECTING
            self._last_error = None
            try:
                devices = await self.discover()
                use_depthai = self._use_depthai(devices)
                if use_depthai:
                    self._pipeline = self._build_pipeline_for_connect(True)
                elif self._camera_cfg.mock_when_unavailable:
                    self._pipeline = self._build_pipeline_for_connect(False)
                else:
                    raise RuntimeError(
                        "No OAK camera found and mock_when_unavailable=false. "
                        "PoE: set camera.device_ip — see docs/POE_SETUP.md"
                    )

                target_id = device_id or self._camera_cfg.device_id or None
                if not use_depthai and target_id is None:
                    target_id = "mock-oak-device"

                await self._pipeline.start(target_id)
                if use_depthai and hasattr(self._pipeline, "_device_id"):
                    self._connected_device_id = getattr(self._pipeline, "_device_id", None) or target_id
                else:
                    self._connected_device_id = target_id
                self._state = CameraConnectionState.CONNECTED
                self._start_watchdog()
            except Exception as exc:
                self._state = CameraConnectionState.ERROR
                self._last_error = str(exc)
                logger.error("Camera connect failed: %s", exc)
            return self.get_status()

    async def disconnect(self) -> CameraStatus:
        async with self._lock:
            self._stop_watchdog()
            try:
                await self._pipeline.stop()
            except Exception as exc:
                self._last_error = str(exc)
            self._state = CameraConnectionState.DISCONNECTED
            self._connected_device_id = None
            self._recording = False
            return self.get_status()

    def get_status(self) -> CameraStatus:
        devices = discover_devices() if depthai_available() else []
        return CameraStatus(
            connection_state=self._state,
            connected_device_id=self._connected_device_id,
            recording=self._recording,
            capture_flags=self._capture_flags,
            pipeline_mode=self._pipeline.mode,  # type: ignore[arg-type]
            available_devices=devices,
            last_error=self._last_error,
            depthai_available=depthai_available(),
        )

    async def ensure_connected(self) -> None:
        if self._state != CameraConnectionState.CONNECTED:
            if self._camera_cfg.auto_connect:
                await self.connect(self._camera_cfg.device_id or None)
            elif self._camera_cfg.mock_when_unavailable:
                await self.connect(None)

    async def begin_chunk(
        self,
        chunk_id: str,
        chunk_path: Path,
    ) -> None:
        if chunk_id in self._sessions:
            return

        await self.ensure_connected()
        if self._state != CameraConnectionState.CONNECTED:
            return

        flags = self._capture_flags
        if not (flags.rgb or flags.tof or flags.pose):
            return

        session = ChunkCaptureSession(
            chunk_id=chunk_id,
            chunk_path=chunk_path,
            flags=flags,
        )
        self._sessions[chunk_id] = session
        self._recording = True
        await self._pipeline.begin_chunk(session)

    async def finalize_source(
        self,
        chunk_id: str,
        source_id: str,
    ) -> SourceManifest | None:
        session = self._sessions.get(chunk_id)
        if session is None:
            return None

        flags = self._capture_flags
        enabled = {"rgb": flags.rgb, "tof": flags.tof, "pose": flags.pose}.get(source_id, False)
        if not enabled:
            return None

        if chunk_id not in self._session_manifests:
            manifests = await self._pipeline.end_chunk(session)
            self._session_manifests[chunk_id] = manifests
            self._sessions.pop(chunk_id, None)
            self._recording = bool(self._sessions)

        data = self._session_manifests.get(chunk_id, {}).get(source_id)
        if data is None:
            return None
        return SourceManifest(path=data["path"], stub=data.get("mock", False), extra=data)

    async def abort_chunk(self, chunk_id: str) -> None:
        session = self._sessions.pop(chunk_id, None)
        if session is not None:
            session.running = False
            if session.task is not None:
                session.task.cancel()
        self._session_manifests.pop(chunk_id, None)

    def _start_watchdog(self) -> None:
        if not self._camera_cfg.reconnect_enabled:
            return
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    def _stop_watchdog(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        interval = self._camera_cfg.reconnect_interval_sec
        while True:
            try:
                await asyncio.sleep(interval)
                if self._state != CameraConnectionState.CONNECTED:
                    continue
                if self._pipeline.is_alive():
                    continue
                logger.warning("Camera link lost — attempting hot-plug reconnect")
                await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Camera watchdog error: %s", exc)

    async def _reconnect(self) -> None:
        async with self._lock:
            device_id = self._connected_device_id
            if device_id is None:
                return
            try:
                await self._pipeline.stop()
            except Exception:
                pass
            try:
                devices = await self.discover()
                use_depthai = self._use_depthai(devices)
                self._pipeline = self._build_pipeline_for_connect(use_depthai)
                await self._pipeline.start(device_id)
                self._state = CameraConnectionState.CONNECTED
                self._last_error = None
                logger.info("Camera reconnected: %s", device_id)
            except Exception as exc:
                self._state = CameraConnectionState.ERROR
                self._last_error = str(exc)
                logger.error("Camera reconnect failed: %s", exc)
