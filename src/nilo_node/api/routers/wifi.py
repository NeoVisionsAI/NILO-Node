"""WiFi access point API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.config.models import AppConfig
from nilo_node.config.persistence import patch_wifi_section, resolve_config_path
from nilo_node.network.wifi_manager import WifiApManager


class WifiConfigUpdate(BaseModel):
    password: str | None = None
    channel: int | None = Field(default=None, ge=1, le=13)
    ssid_prefix: str | None = None


def create_wifi_router(
    config: AppConfig,
    wifi: WifiApManager,
    config_path: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/wifi", tags=["wifi"])
    auth = Depends(require_auth(config))
    cfg_path = resolve_config_path(config, Path(config_path) if config_path else None)

    @router.get("/status", dependencies=[auth])
    async def wifi_status() -> dict[str, Any]:
        return wifi.get_status().model_dump(mode="json")

    @router.post("/restart", dependencies=[auth])
    async def wifi_restart() -> dict[str, Any]:
        status = await wifi.restart()
        return status.model_dump(mode="json")

    @router.patch("/config", dependencies=[auth])
    async def wifi_config(body: WifiConfigUpdate) -> dict[str, Any]:
        updates = body.model_dump(exclude_none=True)
        if updates:
            patch_wifi_section(cfg_path, updates)
            if body.password is not None:
                wifi._wifi.password = body.password
            if body.channel is not None:
                wifi._wifi.channel = body.channel
            if body.ssid_prefix is not None:
                wifi._wifi.ssid_prefix = body.ssid_prefix
        status = await wifi.restart()
        return {"wifi": status.model_dump(mode="json"), "updated": updates}

    return router
