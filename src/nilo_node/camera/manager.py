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
from nilo_node.camera.model_runtime import CameraModelRuntime
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
        self._last_preview_error: str | None = None
        models_root = Path(config.storage.base_path) / "models" / "pose"
        self._model_runtime = CameraModelRuntime(models_root)

    def get_model_state(self) -> dict[str, object]:
        return self._model_runtime.state.to_dict()

    async def load_pose_model(
        self,
        backend: str,
        *,
        placement: str = "host",
    ) -> dict[str, object]:
        async with self._lock:
            result = await self._model_runtime.load(
                backend,  # type: ignore[arg-type]
                placement=placement,  # type: ignore[arg-type]
            )
            if backend in ("mediapipe", "yolo"):
                self._camera_cfg.pose_backend = backend  # type: ignore[assignment]
            return result

    async def unload_pose_model(self) -> dict[str, object]:
        async with self._lock:
            return await self._model_runtime.unload()

    async def test_loaded_model(self) -> dict[str, object]:
        state = self._model_runtime.state
        if not state.loaded:
            return {"ok": False, "error": "No hay modelo cargado — pulsa «Cargar modelo» primero"}
        result = await self.test_pose()
        result["model"] = state.to_dict()
        result["placement"] = state.placement
        return result

    @property
    def last_preview_error(self) -> str | None:
        return self._last_preview_error

    def _encode_jpeg(self, frame: object, *, quality: int = 80) -> bytes | None:
        try:
            import cv2

            ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                return jpeg.tobytes()
        except ImportError:
            self._last_preview_error = "OpenCV (cv2) no disponible en el contenedor"
        except Exception as exc:
            self._last_preview_error = f"Error codificando JPEG: {exc}"
        return None

    @staticmethod
    def _depth_to_bgr(depth: object) -> object:
        import cv2
        import numpy as np

        arr = np.asarray(depth, dtype=np.float32)
        arr = np.nan_to_num(arr)
        if arr.size == 0:
            return np.zeros((480, 640, 3), dtype=np.uint8)
        lo, hi = float(arr.min()), float(arr.max())
        if hi > lo:
            norm = ((arr - lo) / (hi - lo) * 255.0).astype(np.uint8)
        else:
            norm = np.zeros(arr.shape, dtype=np.uint8)
        if norm.ndim == 2:
            return cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
        return norm

    def _synthetic_preview_frame(self, label: str) -> bytes | None:
        try:
            import cv2
            import numpy as np

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                label,
                (24, 240),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (200, 200, 200),
                2,
            )
            return self._encode_jpeg(frame)
        except ImportError:
            self._last_preview_error = "OpenCV (cv2) no disponible en el contenedor"
            return None

    async def get_preview_jpeg(
        self,
        *,
        wait_for_frame: bool = True,
        stream: str = "auto",
    ) -> bytes | None:
        self._last_preview_error = None
        stream = stream if stream in ("auto", "rgb", "tof") else "auto"

        if self._state != CameraConnectionState.CONNECTED:
            self._last_preview_error = "Cámara no conectada — pulsa Conectar en el portal"
            return None

        pipeline = self._pipeline

        if getattr(pipeline, "mode", None) == "mock":
            label = "Mock RGB" if stream != "tof" else "Mock ToF"
            return self._synthetic_preview_frame(label)

        if getattr(pipeline, "mode", None) == "depthai":
            if getattr(pipeline, "_use_synthetic", False):
                device = getattr(pipeline, "_device_id", None) or "OAK"
                label = f"OAK {device} — sintético ({stream})"
                return self._synthetic_preview_frame(label)

            session = getattr(pipeline, "_session", None)
            if session is None:
                self._last_preview_error = "Pipeline DepthAI sin sesión activa — reconecta la cámara"
                return None

            attempts = 25 if wait_for_frame else 10
            last_bundle = None
            for attempt in range(attempts):
                bundle = await asyncio.to_thread(session.poll)
                last_bundle = bundle
                if bundle is None:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(0.12)
                    continue

                if stream in ("auto", "rgb") and bundle.rgb is not None:
                    jpeg = self._encode_jpeg(bundle.rgb)
                    if jpeg:
                        return jpeg

                if stream in ("auto", "tof") and bundle.depth is not None:
                    bgr = self._depth_to_bgr(bundle.depth)
                    jpeg = self._encode_jpeg(bgr)
                    if jpeg:
                        if stream == "tof" or bundle.rgb is None:
                            self._last_preview_error = None
                        return jpeg

                if attempt + 1 < attempts:
                    await asyncio.sleep(0.12)

            if last_bundle is None:
                self._last_preview_error = (
                    "Sin respuesta del dispositivo OAK — revisa PoE/USB y la IP en configuración"
                )
            elif not getattr(session, "is_open", True):
                self._last_preview_error = "Enlace DepthAI cerrado — pulsa Conectar de nuevo"
            elif stream == "rgb":
                self._last_preview_error = "Sin frame RGB disponible"
            elif stream == "tof":
                self._last_preview_error = "Sin frame ToF/disponible"
            else:
                self._last_preview_error = (
                    "Cámara conectada pero sin frames RGB/ToF aún — "
                    "espera unos segundos o revisa cable/red PoE"
                )
            logger.debug("Camera preview unavailable: %s", self._last_preview_error)
            return None

        self._last_preview_error = "Preview no disponible para este pipeline"
        return None

    async def test_pose(self) -> dict[str, object]:
        """Capture one frame and run the configured pose engine (on demand)."""
        import base64
        import time

        import cv2
        import numpy as np

        from nilo_node.camera.pose.mediapipe_engine import draw_pose_landmarks

        jpeg = await self.get_preview_jpeg(wait_for_frame=True, stream="rgb")
        if jpeg is None:
            return {
                "ok": False,
                "frame_available": False,
                "engine_available": False,
                "error": self._last_preview_error or "No hay frame disponible",
            }

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {
                "ok": False,
                "frame_available": False,
                "engine_available": False,
                "error": "No se pudo decodificar el frame JPEG",
            }

        engine = self._build_pose_engine_for_test()
        landmarks = engine.process(frame, time.time())
        visible = int(np.sum(landmarks[:, 3] > 0.5))
        engine_available = getattr(engine, "available", engine.engine_id != "stub")

        result: dict[str, object] = {
            "ok": engine_available,
            "frame_available": True,
            "engine_available": engine_available,
            "engine_id": engine.engine_id,
            "pose_detected": visible > 0,
            "landmarks_detected": visible,
            "landmark_count": engine.landmark_count,
            "pose_backend": self._camera_cfg.pose_backend,
        }

        if visible > 0:
            annotated = draw_pose_landmarks(frame, landmarks)
            ann_jpeg = self._encode_jpeg(annotated)
            if ann_jpeg:
                result["annotated_jpeg_base64"] = base64.b64encode(ann_jpeg).decode("ascii")

        if not engine_available:
            result["error"] = (
                "Motor de pose no disponible — revisa mediapipe en el contenedor"
            )
        elif visible == 0:
            result["message"] = "MediaPipe activo pero no detectó cuerpo en este frame"

        return result

    def _build_pose_engine_for_test(self):
        from nilo_node.camera.pose.factory import build_pose_engine

        state = self._model_runtime.state
        cfg = self._camera_cfg.model_copy(deep=True)
        if state.loaded and state.backend in ("mediapipe", "yolo"):
            cfg.pose_backend = state.backend  # type: ignore[assignment]
        return build_pose_engine(cfg)

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
        preview_synthetic = False
        if self._state == CameraConnectionState.CONNECTED:
            preview_synthetic = bool(getattr(self._pipeline, "_use_synthetic", False))
        return CameraStatus(
            connection_state=self._state,
            connected_device_id=self._connected_device_id,
            recording=self._recording,
            capture_flags=self._capture_flags,
            pipeline_mode=self._pipeline.mode,  # type: ignore[arg-type]
            available_devices=devices,
            last_error=self._last_error,
            last_preview_error=self._last_preview_error,
            depthai_available=depthai_available(),
            preview_synthetic=preview_synthetic,
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
