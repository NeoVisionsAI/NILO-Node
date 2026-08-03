"""OAK camera management API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from nilo_node.api.deps import require_auth
from nilo_node.camera.manager import CameraManager
from nilo_node.config.models import AppConfig


class ConnectRequest(BaseModel):
    device_id: str | None = None


def create_camera_router(config: AppConfig, camera: CameraManager) -> APIRouter:
    router = APIRouter(prefix="/api/v1/camera", tags=["camera"])
    auth = Depends(require_auth(config))

    @router.get("/discover", dependencies=[auth])
    async def discover_cameras() -> dict[str, Any]:
        devices = await camera.discover()
        return {
            "depthai_available": camera.get_status().depthai_available,
            "devices": [d.model_dump() for d in devices],
        }

    @router.post("/connect", dependencies=[auth])
    async def connect_camera(body: ConnectRequest) -> dict[str, Any]:
        status = await camera.connect(body.device_id)
        return status.model_dump()

    @router.post("/disconnect", dependencies=[auth])
    async def disconnect_camera() -> dict[str, Any]:
        status = await camera.disconnect()
        return status.model_dump()

    @router.get("/status", dependencies=[auth])
    async def camera_status() -> dict[str, Any]:
        return camera.get_status().model_dump()

    return router
