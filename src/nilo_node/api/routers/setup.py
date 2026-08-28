"""Local setup portal API, login, and persisted settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.config.applier import SettingsApplier
from nilo_node.config.models import AppConfig
from nilo_node.config.runtime_store import RuntimeSettings, RuntimeSettingsStore
from nilo_node.mqtt.service import MqttService
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.util.node_id import node_short_id, verify_setup_login


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    mqtt_topic: str | None = None
    mqtt_events_topic: str | None = None


class CameraSettingsPatch(BaseModel):
    device_id: str | None = None
    device_ip: str | None = None
    connection_mode: Literal["auto", "usb", "poe"] | None = None
    rgb_fps: int | None = Field(default=None, ge=1, le=60)
    tof_fps: int | None = Field(default=None, ge=1, le=60)
    pose_fps: int | None = Field(default=None, ge=1, le=60)
    pose_backend: Literal["mediapipe", "yolo", "custom"] | None = None
    auto_connect: bool | None = None
    mock_when_unavailable: bool | None = None


class WifiSettingsPatch(BaseModel):
    enabled: bool | None = None
    password: str | None = None
    channel: int | None = Field(default=None, ge=1, le=13)
    ssid_prefix: str | None = None


class BluetoothSettingsPatch(BaseModel):
    enabled: bool | None = None
    default_record_on_connect: bool | None = None
    scan_timeout_sec: int | None = Field(default=None, ge=3, le=120)


class MqttSettingsPatch(BaseModel):
    enabled: bool | None = None
    broker_host: str | None = None
    broker_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None


class SettingsPatchRequest(BaseModel):
    camera: CameraSettingsPatch | None = None
    wifi: WifiSettingsPatch | None = None
    bluetooth: BluetoothSettingsPatch | None = None
    mqtt: MqttSettingsPatch | None = None


def create_setup_router(
    config: AppConfig,
    node_id: str,
    camera: CameraManager,
    bluetooth: BluetoothManager,
    wifi: WifiApManager,
    settings_store: RuntimeSettingsStore,
    settings_applier: SettingsApplier,
    mqtt: MqttService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/setup", tags=["setup"])
    auth = Depends(require_auth(config))

    def _check_login(username: str, password: str) -> None:
        if verify_setup_login(
            config=config,
            node_id=node_id,
            username=username,
            password=password,
        ):
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    @router.post("/login")
    async def login(body: LoginRequest) -> LoginResponse:
        _check_login(body.username.strip(), body.password)
        token = config.local_api.auth_token
        if not token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="NILO_LOCAL_API_TOKEN not configured",
            )
        mqtt_status = mqtt.get_status() if mqtt else None
        return LoginResponse(
            token=token,
            username=body.username.strip() or node_short_id(node_id),
            mqtt_topic=mqtt_status.subscribe_topic if mqtt_status else None,
            mqtt_events_topic=mqtt_status.events_topic if mqtt_status else None,
        )

    @router.get("/dashboard", dependencies=[auth])
    async def dashboard() -> dict[str, Any]:
        cam_status = camera.get_status()
        bt_status = bluetooth.get_status()
        wifi_status = wifi.get_status()
        mqtt_status = mqtt.get_status().model_dump(mode="json") if mqtt else None
        settings = settings_store.load()
        return {
            "node_id": node_id,
            "camera": cam_status.model_dump(),
            "bluetooth": bt_status.model_dump(mode="json"),
            "wifi": wifi_status.model_dump(mode="json"),
            "mqtt": mqtt_status,
            "settings": settings.model_dump(mode="json"),
            "setup_portal": "/setup/",
        }

    @router.get("/settings", dependencies=[auth])
    async def get_settings() -> dict[str, Any]:
        settings = settings_store.load()
        return {
            "settings": settings.model_dump(mode="json"),
            "live": {
                "camera": config.camera.model_dump(),
                "wifi": config.wifi.model_dump(),
                "bluetooth": config.bluetooth.model_dump(),
                "mqtt": config.mqtt.model_dump(),
            },
        }

    @router.patch("/settings", dependencies=[auth])
    async def patch_settings(body: SettingsPatchRequest) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if body.camera is not None:
            patch["camera"] = body.camera.model_dump(exclude_none=True)
        if body.wifi is not None:
            patch["wifi"] = body.wifi.model_dump(exclude_none=True)
        if body.bluetooth is not None:
            patch["bluetooth"] = body.bluetooth.model_dump(exclude_none=True)
        if body.mqtt is not None:
            patch["mqtt"] = body.mqtt.model_dump(exclude_none=True)
        if not patch:
            return {"settings": settings_store.load().model_dump(mode="json"), "applied": {}}

        settings = settings_store.merge_and_save(patch)
        applied = await settings_applier.apply(settings)
        return {
            "settings": settings.model_dump(mode="json"),
            "applied": applied,
        }

    return router
