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
            )
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
        if placement == "device" and not self._state.blob_path:
            self._state.message = (
                "Modelo preparado en host; blob Myriad no generado — "
                "usa placement=host o instala blobconverter"
            )
        elif placement == "device":
            self._state.message = "Modelo cargado para inferencia en cámara OAK (blob listo)"
        else:
            self._state.message = f"Modelo {backend} listo en el nodo (CPU/OpenVINO host)"
        self._persist()
        return self._state.to_dict()

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

        toolchain = CameraModelRuntime._resolve_model_toolchain()
        spec = importlib.util.spec_from_file_location("oak_model_toolchain", toolchain)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
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
