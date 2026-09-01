"""OAK camera management API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.camera.manager import CameraManager
from nilo_node.config.models import AppConfig, CameraConfig
from nilo_node.config.persistence import merge_camera_config, resolve_config_path, _path_is_writable


class ConnectRequest(BaseModel):
    device_id: str | None = None


class CameraConfigUpdate(BaseModel):
    device_id: str | None = None
    device_ip: str | None = None
    connection_mode: Literal["auto", "usb", "poe"] | None = None
    rgb_fps: int | None = Field(default=None, ge=1, le=60)
    tof_fps: int | None = Field(default=None, ge=1, le=60)
    pose_fps: int | None = Field(default=None, ge=1, le=60)
    pose_backend: Literal["mediapipe", "yolo", "custom"] | None = None
    auto_connect: bool | None = None
    mock_when_unavailable: bool | None = None


class ModelLoadRequest(BaseModel):
    backend: Literal["mediapipe", "yolo"] | None = None
    placement: Literal["host", "device"] = "host"


def create_camera_router(
    config: AppConfig,
    camera: CameraManager,
    config_path: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/camera", tags=["camera"])
    auth = Depends(require_auth(config))
    cfg_path = resolve_config_path(config, Path(config_path) if config_path else None)

    @router.get("/discover", dependencies=[auth])
    async def discover_cameras() -> dict[str, Any]:
        devices = await camera.discover()
        return {
            "depthai_available": camera.get_status().depthai_available,
            "devices": [d.model_dump() for d in devices],
        }

    @router.post("/connect", dependencies=[auth])
    async def connect_camera(body: ConnectRequest) -> dict[str, Any]:
        status_obj = await camera.connect(body.device_id)
        return status_obj.model_dump()

    @router.post("/disconnect", dependencies=[auth])
    async def disconnect_camera() -> dict[str, Any]:
        status_obj = await camera.disconnect()
        return status_obj.model_dump()

    @router.post("/refresh", dependencies=[auth])
    async def refresh_camera() -> dict[str, Any]:
        """Refresh connection status (no disconnect)."""
        return camera.get_status().model_dump()

    @router.get("/status", dependencies=[auth])
    async def camera_status() -> dict[str, Any]:
        status_data = camera.get_status().model_dump()
        status_data["model"] = camera.get_model_state()
        return status_data

    @router.get("/preview", dependencies=[auth])
    async def camera_preview(
        stream: Literal["auto", "rgb", "tof"] = Query(default="auto"),
    ) -> Response:
        jpeg = await camera.get_preview_jpeg(wait_for_frame=False, stream=stream)
        if jpeg is None:
            cam_status = camera.get_status()
            detail = (
                camera.last_preview_error
                or cam_status.last_error
                or "Camera not connected or preview unavailable"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        return Response(content=jpeg, media_type="image/jpeg")

    @router.post("/snapshot", dependencies=[auth])
    async def camera_snapshot(
        stream: Literal["auto", "rgb", "tof"] = Query(default="rgb"),
    ) -> Response:
        """Capture a single JPEG frame (rgb or tof colormap)."""
        jpeg = await camera.get_preview_jpeg(wait_for_frame=True, stream=stream)
        if jpeg is None:
            cam_status = camera.get_status()
            detail = (
                camera.last_preview_error
                or cam_status.last_error
                or "No hay frame — conecta la cámara primero"
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        return Response(content=jpeg, media_type="image/jpeg")

    @router.post("/pose-test", dependencies=[auth])
    async def camera_pose_test() -> dict[str, Any]:
        """Capture one frame and run pose inference (no live streaming)."""
        result = await camera.test_loaded_model()
        if not result.get("frame_available", True) and result.get("error"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(result["error"]),
            )
        if result.get("frame_available") and not result.get("engine_available"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(result.get("error") or "Motor de pose no disponible"),
            )
        return result

    @router.get("/model", dependencies=[auth])
    async def camera_model_status() -> dict[str, Any]:
        state = camera.get_model_state()
        loaded = bool(state.get("loaded"))
        placement = state.get("placement") or "host"
        return {
            **state,
            "model_loaded": loaded,
            "loaded_on_device": loaded and placement == "device",
            "loaded_on_host": loaded and placement == "host",
        }

    @router.post("/model/load", dependencies=[auth])
    async def camera_model_load(body: ModelLoadRequest) -> dict[str, Any]:
        backend = body.backend or camera.get_status().model_dump().get("pose_backend")
        if backend not in ("mediapipe", "yolo"):
            backend = config.camera.pose_backend
        if backend not in ("mediapipe", "yolo"):
            raise HTTPException(status_code=422, detail="backend debe ser mediapipe o yolo")
        try:
            return await camera.load_pose_model(backend, placement=body.placement)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/model/unload", dependencies=[auth])
    async def camera_model_unload() -> dict[str, Any]:
        return await camera.unload_pose_model()

    @router.patch("/config", dependencies=[auth])
    async def update_camera_config(body: CameraConfigUpdate) -> dict[str, Any]:
        updates = body.model_dump(exclude_none=True)
        if not updates:
            return {"camera": camera.get_status().model_dump(), "updated": {}}
        new_cfg = merge_camera_config(cfg_path, config.camera, updates)
        camera.apply_config(new_cfg)
        config.camera = new_cfg
        return {
            "camera": camera.get_status().model_dump(),
            "model": camera.get_model_state(),
            "updated": updates,
            "config_path": str(cfg_path),
            "config_writable": _path_is_writable(cfg_path),
        }

    return router
