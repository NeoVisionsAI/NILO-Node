"""Register MQTT command handlers that delegate to node services."""

from __future__ import annotations

from typing import Any

from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.config.applier import SettingsApplier
from nilo_node.config.runtime_store import RuntimeSettingsStore
from nilo_node.mqtt.service import MqttService
from nilo_node.network.wifi_manager import WifiApManager


def wire_mqtt_handlers(
    mqtt: MqttService,
    *,
    camera: CameraManager,
    bluetooth: BluetoothManager,
    wifi: WifiApManager,
    settings_store: RuntimeSettingsStore | None = None,
    settings_applier: SettingsApplier | None = None,
) -> None:
    async def ping(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True}

    async def camera_discover(_payload: dict[str, Any]) -> dict[str, Any]:
        devices = await camera.discover()
        status = camera.get_status()
        return {
            "depthai_available": status.depthai_available,
            "devices": [d.model_dump() for d in devices],
        }

    async def camera_connect(payload: dict[str, Any]) -> dict[str, Any]:
        status = await camera.connect(payload.get("device_id"))
        return status.model_dump()

    async def camera_disconnect(_payload: dict[str, Any]) -> dict[str, Any]:
        status = await camera.disconnect()
        return status.model_dump()

    async def camera_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return camera.get_status().model_dump()

    async def bluetooth_discover(_payload: dict[str, Any]) -> dict[str, Any]:
        devices = await bluetooth.discover()
        return {
            "mock": bluetooth.get_status().mock,
            "devices": [d.model_dump() for d in devices],
        }

    async def bluetooth_connect(payload: dict[str, Any]) -> dict[str, Any]:
        mac = payload.get("mac_address") or payload.get("mac")
        if not mac:
            raise ValueError("mac_address required")
        record = await bluetooth.connect(str(mac), payload.get("device_name"))
        return record.model_dump(mode="json")

    async def bluetooth_disconnect(payload: dict[str, Any]) -> dict[str, Any]:
        mac = payload.get("mac_address") or payload.get("mac")
        if not mac:
            raise ValueError("mac_address required")
        record = await bluetooth.disconnect(str(mac))
        return record.model_dump(mode="json")

    async def bluetooth_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return bluetooth.get_status().model_dump(mode="json")

    async def wifi_status(_payload: dict[str, Any]) -> dict[str, Any]:
        return wifi.get_status().model_dump(mode="json")

    async def wifi_restart(_payload: dict[str, Any]) -> dict[str, Any]:
        status = await wifi.restart()
        return status.model_dump(mode="json")

    async def settings_get(_payload: dict[str, Any]) -> dict[str, Any]:
        if settings_store is None:
            raise RuntimeError("Settings store not available")
        settings = settings_store.load()
        return settings.model_dump(mode="json")

    async def settings_update(payload: dict[str, Any]) -> dict[str, Any]:
        if settings_store is None or settings_applier is None:
            raise RuntimeError("Settings store not available")
        settings = settings_store.merge_and_save(payload)
        applied = await settings_applier.apply(settings)
        return {"settings": settings.model_dump(mode="json"), "applied": applied}

    mqtt.register_handler("ping", ping)
    mqtt.register_handler("camera.discover", camera_discover)
    mqtt.register_handler("camera.connect", camera_connect)
    mqtt.register_handler("camera.disconnect", camera_disconnect)
    mqtt.register_handler("camera.status", camera_status)
    mqtt.register_handler("bluetooth.discover", bluetooth_discover)
    mqtt.register_handler("bluetooth.connect", bluetooth_connect)
    mqtt.register_handler("bluetooth.disconnect", bluetooth_disconnect)
    mqtt.register_handler("bluetooth.status", bluetooth_status)
    mqtt.register_handler("wifi.status", wifi_status)
    mqtt.register_handler("wifi.restart", wifi_restart)
    mqtt.register_handler("settings.get", settings_get)
    mqtt.register_handler("settings.update", settings_update)
