"""OAK camera management API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.camera.manager import CameraManager
from nilo_node.config.models import AppConfig, CameraConfig
from nilo_node.config.persistence import patch_camera_section, resolve_config_path


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

    @router.get("/status", dependencies=[auth])
    async def camera_status() -> dict[str, Any]:
        return camera.get_status().model_dump()

    @router.get("/preview", dependencies=[auth])
    async def camera_preview() -> Response:
        jpeg = await camera.get_preview_jpeg()
        if jpeg is None:
            status = camera.get_status()
            detail = status.last_error or "Camera not connected or preview unavailable"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        return Response(content=jpeg, media_type="image/jpeg")

    @router.post("/snapshot", dependencies=[auth])
    async def camera_snapshot() -> Response:
        """Capture a single JPEG frame (same as preview; explicit action for setup UI)."""
        jpeg = await camera.get_preview_jpeg()
        if jpeg is None:
            st = camera.get_status()
            detail = st.last_error or "No hay frame — conecta la cámara primero"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        return Response(content=jpeg, media_type="image/jpeg")

    @router.patch("/config", dependencies=[auth])
    async def update_camera_config(body: CameraConfigUpdate) -> dict[str, Any]:
        updates = body.model_dump(exclude_none=True)
        if not updates:
            return {"camera": camera.get_status().model_dump(), "updated": {}}
        new_cfg = patch_camera_section(cfg_path, updates)
        camera.apply_config(new_cfg)
        return {
            "camera": camera.get_status().model_dump(),
            "updated": updates,
            "config_path": str(cfg_path),
        }

    return router
