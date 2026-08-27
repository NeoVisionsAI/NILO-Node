"""Persist runtime configuration patches to YAML files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from nilo_node.camera.oak_settings import default_oak_local_path, save_oak_connection_settings
from nilo_node.camera.oak_settings import OakConnectionSettings
from nilo_node.config.loader import load_config
from nilo_node.config.models import AppConfig, CameraConfig

logger = logging.getLogger(__name__)


def resolve_config_path(config: AppConfig, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    import os

    return Path(os.environ.get("NILO_CONFIG_PATH", "/etc/nilo-node/nilo-node.yaml"))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def patch_camera_section(config_path: Path, updates: dict[str, Any]) -> CameraConfig:
    """Merge camera updates into YAML and return validated CameraConfig."""
    data = _load_yaml(config_path)
    camera = dict(data.get("camera") or {})
    camera.update({k: v for k, v in updates.items() if v is not None})
    data["camera"] = camera
    _write_yaml(config_path, data)

    merged = load_config(config_path)
    oak_fields = {
        "device_ip": camera.get("device_ip", ""),
        "device_id": camera.get("device_id", ""),
        "connection_mode": camera.get("connection_mode", "auto"),
    }
    try:
        save_oak_connection_settings(OakConnectionSettings(**oak_fields), path=default_oak_local_path())
    except OSError as exc:
        logger.warning("Could not sync oak.local.yaml: %s", exc)

    logger.info("Updated camera config in %s", config_path)
    return merged.camera


def patch_wifi_section(config_path: Path, updates: dict[str, Any]) -> dict[str, Any]:
    data = _load_yaml(config_path)
    wifi = dict(data.get("wifi") or {})
    wifi.update({k: v for k, v in updates.items() if v is not None})
    data["wifi"] = wifi
    _write_yaml(config_path, data)
    logger.info("Updated wifi config in %s", config_path)
    return wifi
