"""Aggregated device status (camera, Cardmed, WiFi)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from nilo_node.api.deps import require_auth
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.config.models import AppConfig
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.state.repository import StateRepository


def create_devices_router(
    config: AppConfig,
    repo: StateRepository,
    camera: CameraManager,
    cardmed: CardmedService,
    wifi: WifiApManager,
    bluetooth: BluetoothManager,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/devices", tags=["devices"])
    auth = Depends(require_auth(config))

    @router.get("", dependencies=[auth])
    async def list_devices() -> dict[str, Any]:
        stored = repo.list_devices()
        return {
            "camera": camera.get_status().model_dump(mode="json"),
            "cardmed": cardmed.get_status().model_dump(mode="json"),
            "wifi": wifi.get_status().model_dump(mode="json"),
            "bluetooth": bluetooth.get_status().model_dump(mode="json"),
            "registry": stored,
        }

    return router
