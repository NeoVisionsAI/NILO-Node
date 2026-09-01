"""Pose model prepare/load state for OAK (host CPU or Myriad blob)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Placement = Literal["host", "device"]
Backend = Literal["mediapipe", "yolo"]


@dataclass
class ModelRuntimeState:
    loaded: bool = False
    backend: str | None = None
    placement: Placement = "host"
    manifest_path: str | None = None
    blob_path: str | None = None
    openvino_xml: str | None = None
    message: str | None = None
    last_error: str | None = None
    engine_available: bool = False

    def inference_ready(self) -> bool:
        """True when host pose-test / CPU inference can run."""
        return self.loaded and self.engine_available

    def device_blob_ready(self) -> bool:
        """True when Myriad blob is ready for on-camera YOLO inference."""
        if not self.loaded:
            return False
        if self.placement != "device":
            return True
        if self.backend == "mediapipe":
            return True
        if self.backend == "yolo":
            return bool(self.blob_path)
        return False

    def refresh_message(self) -> None:
        if not self.loaded:
            return
        backend = self.backend or "?"
        if not self.engine_available:
            hint = self.last_error or "Revisa dependencias del contenedor (OpenGL/EGL/GLES, mediapipe)"
            self.message = f"Modelo {backend} preparado pero motor no disponible — {hint}"
            return
        if backend == "mediapipe":
            self.message = (
                "MediaPipe listo — landmarks en CPU del nodo con frames de la OAK"
                if self.placement == "device"
                else "MediaPipe listo en el nodo (CPU)"
            )
            return
        if backend == "yolo" and self.placement == "device" and not self.blob_path:
            self.message = (
                "YOLO listo en CPU del nodo; blob Myriad pendiente para inferencia en OAK "
                "(requiere blobconverter)"
            )
        elif backend == "yolo" and self.placement == "device":
            self.message = "YOLO cargado para inferencia en cámara OAK (blob listo)"
        elif backend == "yolo":
            self.message = "YOLO listo en el nodo (CPU/Ultralytics)"
        else:
            self.message = f"Modelo {backend} listo"

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "backend": self.backend,
            "placement": self.placement,
            "manifest_path": self.manifest_path,
            "blob_path": self.blob_path,
            "openvino_xml": self.openvino_xml,
            "message": self.message,
            "last_error": self.last_error,
            "engine_available": self.engine_available,
            "inference_ready": self.inference_ready(),
            "device_blob_ready": self.device_blob_ready(),
        }


class CameraModelRuntime:
    """Prepare OpenVINO/blob artifacts and track load state for the portal."""

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = models_dir
        self._models_dir.mkdir(parents=True, exist_ok=True)
        self._state = ModelRuntimeState()
        self._state_path = self._models_dir / "runtime-state.json"
        self._load_persisted()

    @property
    def state(self) -> ModelRuntimeState:
        return self._state

    def _load_persisted(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._state = ModelRuntimeState(
                loaded=bool(data.get("loaded")),
                backend=data.get("backend"),
                placement=data.get("placement") or "host",
                manifest_path=data.get("manifest_path"),
                blob_path=data.get("blob_path"),
                openvino_xml=data.get("openvino_xml"),
                message=data.get("message"),
                last_error=data.get("last_error"),
                engine_available=bool(data.get("engine_available")),
            )
            if self._state.loaded:
                if not self._state.engine_available:
                    self.refresh_engine_probe()
                else:
                    self._state.refresh_message()
                self._persist()
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not load model runtime state: %s", exc)

    def _persist(self) -> None:
        self._state_path.write_text(
            json.dumps(self._state.to_dict(), indent=2),
            encoding="utf-8",
        )

    async def load(
        self,
        backend: Backend,
        *,
        placement: Placement = "host",
        build_blob: bool | None = None,
    ) -> dict[str, Any]:
        self._state.last_error = None
        self._state.backend = backend
        self._state.placement = placement
        want_blob = build_blob if build_blob is not None else placement == "device"

        try:
            manifest = await self._prepare_backend(backend, blob=want_blob)
        except Exception as exc:
            self._state.loaded = False
            self._state.last_error = str(exc)
            self._persist()
            raise

        self._state.loaded = True
        self._state.manifest_path = str(manifest.get("manifest_dir", ""))
        self._state.openvino_xml = manifest.get("openvino_xml")
        self._state.blob_path = manifest.get("blob")
        self.refresh_engine_probe()
        self._persist()
        return self._state.to_dict()

    def refresh_engine_probe(self) -> None:
        if not self._state.loaded or not self._state.backend:
            self._state.engine_available = False
            return
        from nilo_node.camera.pose.factory import probe_pose_engine

        available, err = probe_pose_engine(self._state.backend)
        self._state.engine_available = available
        if err:
            self._state.last_error = err
        elif available:
            self._state.last_error = None
        self._state.refresh_message()

    def set_engine_available(self, available: bool, *, error: str | None = None) -> None:
        self._state.engine_available = available
        if error:
            self._state.last_error = error
        elif available:
            self._state.last_error = None
        if self._state.loaded:
            self._state.refresh_message()
        self._persist()

    async def unload(self) -> dict[str, Any]:
        self._state = ModelRuntimeState(message="Modelo descargado")
        self._persist()
        return self._state.to_dict()

    async def _prepare_backend(self, backend: Backend, *, blob: bool) -> dict[str, Any]:
        import asyncio

        model_dir = self._models_dir / backend
        model_dir.mkdir(parents=True, exist_ok=True)

        if backend == "mediapipe":
            from nilo_node.camera.pose.mediapipe_engine import ensure_pose_landmarker_model

            path = await asyncio.to_thread(ensure_pose_landmarker_model)
            manifest = {
                "manifest_dir": str(model_dir),
                "backend": backend,
                "model_file": str(path),
                "openvino_xml": None,
                "blob": None,
            }
            (model_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return manifest

        return await asyncio.to_thread(self._prepare_yolo, model_dir, blob)

    @staticmethod
    def _resolve_model_toolchain() -> Path:
        import os

        from nilo_node.camera.oak_settings import find_repo_root

        candidates: list[Path] = []
        env_path = os.environ.get("OAK_TOOLCHAIN_PATH")
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend(
            [
                Path("/app/scripts/oak/model_toolchain.py"),
                Path(os.environ.get("NILO_INSTALL_DIR", "")) / "scripts/oak/model_toolchain.py",
            ]
        )
        repo = find_repo_root(Path(__file__))
        if repo is not None:
            candidates.append(repo / "scripts/oak/model_toolchain.py")
        candidates.append(Path(__file__).resolve().parents[3] / "scripts/oak/model_toolchain.py")

        tried: list[str] = []
        for candidate in candidates:
            if not candidate:
                continue
            tried.append(str(candidate))
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(
            "model_toolchain no encontrado; probado: " + ", ".join(tried)
        )

    @staticmethod
    def _prepare_yolo(model_dir: Path, blob: bool) -> dict[str, Any]:
        import importlib.util
        import sys

        toolchain = CameraModelRuntime._resolve_model_toolchain()
        module_name = "oak_model_toolchain"
        spec = importlib.util.spec_from_file_location(module_name, toolchain)
        if spec is None or spec.loader is None:
            raise ImportError(f"No se pudo cargar model_toolchain desde {toolchain}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

        weights = model_dir / "yolov8n-pose.pt"
        if not weights.is_file():
            from ultralytics import YOLO

            YOLO("yolov8n-pose.pt")
            default = Path.home() / ".cache" / "ultralytics"
            candidates = list(default.rglob("yolov8n-pose.pt")) if default.is_dir() else []
            if candidates:
                weights.write_bytes(candidates[0].read_bytes())
            else:
                raise FileNotFoundError(
                    "Descarga yolov8n-pose.pt primero o ejecuta prepare desde scripts/oak"
                )

        manifest_obj = mod.prepare_yolo(
            weights,
            model_dir,
            "yolo-pose",
            openvino=True,
            blob=blob,
        )
        return {
            "manifest_dir": str(model_dir / "yolo-pose"),
            "backend": "yolo",
            "openvino_xml": manifest_obj.openvino_xml,
            "blob": manifest_obj.blob,
        }
