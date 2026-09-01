"""Local setup portal API, login, and persisted settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
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
from nilo_node.system.metrics import system_metrics
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
    record_rgb: bool | None = None
    record_tof: bool | None = None


class WifiSettingsPatch(BaseModel):
    enabled: bool | None = None
    password: str | None = None
    channel: int | None = Field(default=None, ge=1, le=13)
    ssid_prefix: str | None = None


class BluetoothSettingsPatch(BaseModel):
    enabled: bool | None = None
    default_record_on_connect: bool | None = None
    scan_timeout_sec: int | None = Field(default=None, ge=3, le=120)
    auto_reconnect_interval_sec: int | None = Field(default=None, ge=15, le=3600)


class MqttSettingsPatch(BaseModel):
    enabled: bool | None = None
    broker_host: str | None = None
    broker_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    use_tls: bool | None = None
    topic_template: str | None = None
    events_topic_template: str | None = None


class MonitoringWindowPatch(BaseModel):
    days: list[str] = Field(default_factory=list)
    start_time: str = "09:00"
    end_time: str = "17:00"


class MonitoringSettingsPatch(BaseModel):
    enabled: bool | None = None
    schedule_mode: Literal["always", "fixed_window", "weekly_windows"] | None = None
    period_start: str | None = None
    period_end: str | None = None
    windows: list[MonitoringWindowPatch] | None = None
    window_start: str | None = None
    window_end: str | None = None
    daily_start_time: str | None = None
    daily_end_time: str | None = None
    api_host: str | None = None
    pose_backend: Literal["mediapipe", "yolo", "none"] | None = None
    model_placement: Literal["host", "device"] | None = None
    require_full_pose: bool | None = None
    pose_fps: int | None = Field(default=None, ge=1, le=60)
    required_landmarks: list[int] | None = None
    data_export: dict[str, Any] | None = None


class MonitoringHealthRequest(BaseModel):
    host: str = "nilomed.eu"


class MqttTestRequest(BaseModel):
    broker_host: str | None = None
    broker_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    use_tls: bool | None = None


class SettingsPatchRequest(BaseModel):
    camera: CameraSettingsPatch | None = None
    wifi: WifiSettingsPatch | None = None
    bluetooth: BluetoothSettingsPatch | None = None
    mqtt: MqttSettingsPatch | None = None
    monitoring: MonitoringSettingsPatch | None = None


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
        if mqtt_status is not None:
            mqtt_status["topic_template"] = config.mqtt.topic_template
            mqtt_status["events_topic_template"] = config.mqtt.events_topic_template
        settings = settings_store.load()
        return {
            "node_id": node_id,
            "node_short_id": node_short_id(node_id),
            "system": system_metrics(),
            "camera": cam_status.model_dump(),
            "camera_model": camera.get_model_state(),
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
        if body.monitoring is not None:
            patch["monitoring"] = body.monitoring.model_dump(exclude_none=True)
        if not patch:
            return {"settings": settings_store.load().model_dump(mode="json"), "applied": {}}

        settings = settings_store.merge_and_save(patch)
        applied = await settings_applier.apply(settings)
        return {
            "settings": settings.model_dump(mode="json"),
            "applied": applied,
        }

    @router.post("/monitoring/health-check", dependencies=[auth])
    async def monitoring_health_check(body: MonitoringHealthRequest) -> dict[str, Any]:
        host = body.host.strip().rstrip("/")
        if not host:
            raise HTTPException(status_code=422, detail="Host vacío")
        if "://" in host:
            parsed = urlparse(host)
            base = f"{parsed.scheme}://{parsed.netloc}"
        else:
            base = None

        attempts: list[str] = []
        if base:
            attempts.append(f"{base.rstrip('/')}/api/health")
        else:
            attempts.extend(
                f"https://{host}/api/health",
                f"http://{host}/api/health",
            )

        last_error: str | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=True) as client:
            for url in attempts:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        body_json: Any
                        try:
                            body_json = response.json()
                        except ValueError:
                            body_json = response.text[:500]
                        return {
                            "ok": True,
                            "url": url,
                            "status_code": response.status_code,
                            "body": body_json,
                        }
                    last_error = f"HTTP {response.status_code} en {url}"
                except httpx.HTTPError as exc:
                    last_error = f"{url}: {exc}"

        return {"ok": False, "url": attempts[0] if attempts else None, "error": last_error or "Sin respuesta"}

    @router.post("/mqtt/test-connection", dependencies=[auth])
    async def mqtt_test_connection(body: MqttTestRequest) -> dict[str, Any]:
        mqtt_cfg = config.mqtt.model_copy(deep=True)
        if body.broker_host is not None:
            mqtt_cfg.broker_host = body.broker_host.strip()
        if body.broker_port is not None:
            mqtt_cfg.broker_port = body.broker_port
        if body.username is not None:
            mqtt_cfg.username = body.username
        if body.password is not None:
            mqtt_cfg.password = body.password
        if body.use_tls is not None:
            mqtt_cfg.use_tls = body.use_tls

        host = mqtt_cfg.broker_host.strip()
        if not host:
            raise HTTPException(status_code=422, detail="Host del broker vacío")

        try:
            import aiomqtt
        except ImportError:
            if mqtt_cfg.mock_when_unavailable:
                return {
                    "ok": True,
                    "mock": True,
                    "detail": "aiomqtt no instalado — modo simulación",
                }
            return {"ok": False, "error": "aiomqtt no instalado en el contenedor"}

        import ssl

        tls_context = ssl.create_default_context() if mqtt_cfg.use_tls else None
        try:
            async with aiomqtt.Client(
                hostname=host,
                port=mqtt_cfg.broker_port,
                username=mqtt_cfg.username or None,
                password=mqtt_cfg.password or None,
                tls_context=tls_context,
                timeout=12,
            ) as _client:
                return {
                    "ok": True,
                    "broker": f"{host}:{mqtt_cfg.broker_port}",
                    "tls": mqtt_cfg.use_tls,
                }
        except Exception as exc:
            return {"ok": False, "broker": f"{host}:{mqtt_cfg.broker_port}", "error": str(exc)}

    return router
