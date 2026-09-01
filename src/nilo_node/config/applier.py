"""Apply runtime settings to live services and nilo-node.yaml."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.config.models import AppConfig, CameraConfig
from nilo_node.config.persistence import merge_camera_config, merge_wifi_config, patch_camera_section, resolve_config_path
from nilo_node.config.runtime_store import RuntimeSettings
from nilo_node.network.wifi_manager import WifiApManager

logger = logging.getLogger(__name__)


def _normalize_camera_patch(raw: dict[str, Any], current: CameraConfig) -> dict[str, Any]:
    patch = {k: v for k, v in raw.items() if v is not None}
    defaults = current.defaults.model_dump()
    changed = False
    if "record_rgb" in patch:
        defaults["rgb_enabled"] = bool(patch.pop("record_rgb"))
        changed = True
    if "record_tof" in patch:
        defaults["tof_enabled"] = bool(patch.pop("record_tof"))
        changed = True
    if changed:
        patch["defaults"] = defaults
    return patch


class SettingsApplier:
    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        *,
        camera: CameraManager,
        wifi: WifiApManager,
        bluetooth: BluetoothManager,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._camera = camera
        self._wifi = wifi
        self._bluetooth = bluetooth

    async def apply(self, settings: RuntimeSettings) -> dict[str, Any]:
        applied: dict[str, Any] = {}

        if settings.camera:
            camera_patch = _normalize_camera_patch(settings.camera, self._config.camera)
            camera_cfg = merge_camera_config(
                self._config_path if self._config_path.is_file() else None,
                self._config.camera,
                camera_patch,
            )
            self._camera.apply_config(camera_cfg)
            self._config.camera = camera_cfg
            applied["camera"] = settings.camera

        if settings.wifi:
            wifi_merged = merge_wifi_config(
                self._config_path if self._config_path.is_file() else None,
                self._config.wifi.model_dump(),
                settings.wifi,
            )
            for key, value in wifi_merged.items():
                if hasattr(self._wifi._wifi, key):
                    setattr(self._wifi._wifi, key, value)
            if settings.wifi.get("enabled", self._config.wifi.enabled):
                status = await self._wifi.restart()
                applied["wifi"] = status.model_dump(mode="json")
            else:
                await self._wifi.stop()
                applied["wifi"] = self._wifi.get_status().model_dump(mode="json")

        if settings.bluetooth:
            for key, value in settings.bluetooth.items():
                if hasattr(self._bluetooth._config.bluetooth, key):
                    setattr(self._bluetooth._config.bluetooth, key, value)
            applied["bluetooth"] = settings.bluetooth

        if settings.mqtt:
            for key, value in settings.mqtt.items():
                if hasattr(self._config.mqtt, key):
                    setattr(self._config.mqtt, key, value)
            applied["mqtt"] = settings.mqtt

        if settings.monitoring:
            applied["monitoring"] = settings.monitoring

        logger.info("Applied runtime settings: %s", list(applied.keys()))
        return applied
