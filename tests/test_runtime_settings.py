"""Tests for runtime settings store and setup API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nilo_node.api.app import create_app
from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.config.applier import SettingsApplier
from nilo_node.config.models import AppConfig
from nilo_node.config.runtime_store import RuntimeSettingsStore
from nilo_node.monitoring.controller import CampaignController
from nilo_node.mqtt.handlers import wire_mqtt_handlers
from nilo_node.mqtt.service import MqttService
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.sources.physiology.store import PhysiologyStore
from nilo_node.sources.registry import build_sources, init_camera_manager
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.paths import StoragePaths
from nilo_node.storage.replication.manager import ReplicationManager, enabled_replication_target_ids


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path), "retention_check_interval_sec": 99999},
            "local_api": {
                "auth_token": "api-token",
                "setup_username": "admin",
                "setup_password": "setup-secret",
                "setup_enabled": True,
            },
            "backend": {"enabled": False},
            "wifi": {"enabled": True, "mock_when_unavailable": True},
            "mqtt": {"enabled": False},
            "bluetooth": {"enabled": True, "mock_when_unavailable": True, "adapter": "hci99"},
            "sources": {
                "rgb": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "tof": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "pose": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "audio": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "physiology": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
            },
            "monitoring": {"default_chunk_duration_sec": 300, "schedule_tick_sec": 1},
        }
    )


def _client(tmp_path: Path) -> TestClient:
    config = _config(tmp_path)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    settings_store = RuntimeSettingsStore(db, tmp_path)
    paths = StoragePaths(tmp_path, config.storage.recordings_dir)
    camera = CameraManager(config)
    init_camera_manager(camera)
    physiology_store = PhysiologyStore(config.cardmed, repo)
    bluetooth = BluetoothManager(config, repo)
    sources = build_sources(config, physiology_store=physiology_store)
    backend = BackendClient(config, tmp_path, "node-abc")
    upload_queue = UploadQueueService(config, repo)
    chunk_coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(repo, chunk_coordinator, sources, paths, "node-abc")
    storage_manager = StorageManager(
        config, repo, paths, enabled_target_ids=enabled_replication_target_ids(config)
    )
    replication_manager = ReplicationManager(
        config, repo, paths, backend, storage_manager, upload_queue
    )
    cardmed = CardmedService(config, repo, "node-abc", physiology_store, backend)
    wifi = WifiApManager(config, tmp_path, "node-abc")
    mqtt = MqttService(config, "node-abc")
    cfg_path = tmp_path / "nilo-node.yaml"
    cfg_path.write_text("camera:\n  device_ip: ''\nwifi:\n  enabled: true\n", encoding="utf-8")
    applier = SettingsApplier(config, cfg_path, camera=camera, wifi=wifi, bluetooth=bluetooth)
    wire_mqtt_handlers(mqtt, camera=camera, bluetooth=bluetooth, wifi=wifi, settings_store=settings_store, settings_applier=applier)

    app = create_app(
        "node-abc",
        config,
        controller,
        storage_manager,
        replication_manager,
        camera,
        repo,
        cardmed,
        wifi,
        bluetooth,
        backend,
        upload_queue,
        mqtt_service=mqtt,
        config_path=str(cfg_path),
        settings_store=settings_store,
        settings_applier=applier,
    )
    return TestClient(app)


def test_runtime_settings_persist(tmp_path: Path) -> None:
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    store = RuntimeSettingsStore(db, tmp_path)
    saved = store.merge_and_save({"camera": {"device_ip": "169.254.1.222", "connection_mode": "poe"}})
    assert saved.camera["device_ip"] == "169.254.1.222"
    loaded = store.load()
    assert loaded.camera["connection_mode"] == "poe"
    yaml_path = tmp_path / "config" / "runtime-settings.yaml"
    assert yaml_path.is_file()


def test_setup_login_username_password(tmp_path: Path) -> None:
    client = _client(tmp_path)
    bad = client.post("/api/v1/setup/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    ok = client.post(
        "/api/v1/setup/login",
        json={"username": "admin", "password": "setup-secret"},
    )
    assert ok.status_code == 200
    assert ok.json()["token"] == "api-token"


def test_patch_settings_via_api(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {"Authorization": "Bearer api-token"}
    res = client.patch(
        "/api/v1/setup/settings",
        headers=headers,
        json={"camera": {"device_ip": "10.0.0.5", "connection_mode": "poe"}},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["settings"]["camera"]["device_ip"] == "10.0.0.5"

    db = Database(tmp_path / "nilo-node.db")
    row = db.connect().execute("SELECT payload FROM runtime_settings WHERE id = 1").fetchone()
    assert row is not None
    data = json.loads(row["payload"])
    assert data["camera"]["device_ip"] == "10.0.0.5"


def test_patch_settings_readonly_config(tmp_path: Path) -> None:
    client = _client(tmp_path)
    cfg_path = tmp_path / "nilo-node.yaml"
    cfg_path.chmod(0o444)
    headers = {"Authorization": "Bearer api-token"}
    res = client.patch(
        "/api/v1/setup/settings",
        headers=headers,
        json={"camera": {"pose_backend": "mediapipe"}},
    )
    assert res.status_code == 200
    assert res.json()["settings"]["camera"]["pose_backend"] == "mediapipe"

    status = client.get("/api/v1/camera/model", headers=headers)
    assert status.status_code == 200
    cfg_path.chmod(0o644)
