"""DataSource plugin registry."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from nilo_node.config.models import AppConfig
from nilo_node.sources.base import DataSource
from nilo_node.sources.stub import StubSource

if TYPE_CHECKING:
    from nilo_node.bluetooth.manager import BluetoothManager
    from nilo_node.camera.manager import CameraManager

_camera_manager: CameraManager | None = None
_bluetooth_manager: BluetoothManager | None = None


def init_camera_manager(manager: CameraManager) -> None:
    global _camera_manager
    _camera_manager = manager


def get_camera_manager() -> CameraManager:
    if _camera_manager is None:
        raise RuntimeError("CameraManager not initialized")
    return _camera_manager


def _load_class(dotted_path: str) -> type:
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def init_bluetooth_manager(manager: "BluetoothManager") -> None:
    global _bluetooth_manager
    _bluetooth_manager = manager


def get_bluetooth_manager() -> "BluetoothManager":
    if _bluetooth_manager is None:
        raise RuntimeError("BluetoothManager not initialized")
    return _bluetooth_manager


def build_sources(
    config: AppConfig,
    *,
    physiology_store: "PhysiologyStore | None" = None,
    bluetooth_manager: "BluetoothManager | None" = None,
) -> list[DataSource]:
    from nilo_node.sources.physiology.store import PhysiologyStore

    sources: list[DataSource] = []
    camera_source_ids = {"rgb", "tof", "pose"}

    for source_id, source_cfg in config.sources.items():
        if not source_cfg.enabled:
            continue
        cls = _load_class(source_cfg.plugin)
        if source_id in camera_source_ids and _camera_manager is not None:
            instance = cls(source_id, _camera_manager)
        elif source_id == "physiology":
            if physiology_store is None:
                raise RuntimeError("PhysiologyStore required for physiology source")
            assert isinstance(physiology_store, PhysiologyStore)
            instance = cls(source_id, physiology_store)
        elif source_id == "audio":
            bt = bluetooth_manager or _bluetooth_manager
            if bt is None:
                raise RuntimeError("BluetoothManager required for audio source")
            instance = cls(source_id, bt)
        else:
            instance = cls(source_id)
        sources.append(instance)
    return sources
