"""Tests for MQTT service and setup API."""

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


def _minimal_config(tmp_path: Path, token: str = "secret-token") -> AppConfig:
    return AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path), "retention_check_interval_sec": 99999},
            "local_api": {
                "auth_token": token,
                "setup_username": "admin",
                "setup_password": "portal-pass",
                "setup_enabled": True,
            },
            "backend": {"enabled": False},
            "wifi": {"enabled": True, "mock_when_unavailable": True},
            "mqtt": {
                "enabled": True,
                "username": "",
                "password": "",
                "mock_when_unavailable": True,
                "require_message_token": True,
            },
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


def _build_client(tmp_path: Path) -> TestClient:
    config = _minimal_config(tmp_path)
    paths = StoragePaths(tmp_path, config.storage.recordings_dir)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    settings_store = RuntimeSettingsStore(db, tmp_path)
    camera = CameraManager(config)
    init_camera_manager(camera)
    physiology_store = PhysiologyStore(config.cardmed, repo)
    bluetooth = BluetoothManager(config, repo)
    sources = build_sources(config, physiology_store=physiology_store)
    backend = BackendClient(config, tmp_path, "node-abc-123")
    upload_queue = UploadQueueService(config, repo)
    chunk_coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(repo, chunk_coordinator, sources, paths, "node-abc-123")
    storage_manager = StorageManager(
        config, repo, paths, enabled_target_ids=enabled_replication_target_ids(config)
    )
    replication_manager = ReplicationManager(
        config, repo, paths, backend, storage_manager, upload_queue
    )
    cardmed = CardmedService(config, repo, "node-abc-123", physiology_store, backend)
    wifi = WifiApManager(config, tmp_path, "node-abc-123")
    mqtt = MqttService(config, "node-abc-123")
    cfg_path = tmp_path / "nilo-node.yaml"
    cfg_path.write_text("camera:\n  device_ip: ''\n", encoding="utf-8")
    applier = SettingsApplier(config, cfg_path, camera=camera, wifi=wifi, bluetooth=bluetooth)
    wire_mqtt_handlers(mqtt, camera=camera, bluetooth=bluetooth, wifi=wifi, settings_store=settings_store, settings_applier=applier)

    app = create_app(
        "node-abc-123",
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


def test_setup_login_and_dashboard(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    bad = client.post(
        "/api/v1/setup/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert bad.status_code == 401

    ok = client.post(
        "/api/v1/setup/login",
        json={"username": "admin", "password": "portal-pass"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["token"] == "secret-token"
    assert body["mqtt_topic"] == "nilo/node/node-abc-123"

    dash = client.get("/api/v1/setup/dashboard", headers={"Authorization": "Bearer secret-token"})
    assert dash.status_code == 200
    data = dash.json()
    assert data["node_id"] == "node-abc-123"
    assert "settings" in data


def test_setup_login_node_short_and_wifi_password(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    config.wifi.password = "nilo2026"
    config.local_api.setup_username = ""
    config.local_api.setup_password = ""
    paths = StoragePaths(tmp_path, config.storage.recordings_dir)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    settings_store = RuntimeSettingsStore(db, tmp_path)
    camera = CameraManager(config)
    init_camera_manager(camera)
    physiology_store = PhysiologyStore(config.cardmed, repo)
    bluetooth = BluetoothManager(config, repo)
    sources = build_sources(config, physiology_store=physiology_store)
    backend = BackendClient(config, tmp_path, "1f94bda0-0000-0000-0000-000000000000")
    upload_queue = UploadQueueService(config, repo)
    chunk_coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(
        repo, chunk_coordinator, sources, paths, "1f94bda0-0000-0000-0000-000000000000"
    )
    storage_manager = StorageManager(
        config, repo, paths, enabled_target_ids=enabled_replication_target_ids(config)
    )
    replication_manager = ReplicationManager(
        config, repo, paths, backend, storage_manager, upload_queue
    )
    cardmed = CardmedService(
        config, repo, "1f94bda0-0000-0000-0000-000000000000", physiology_store, backend
    )
    wifi = WifiApManager(config, tmp_path, "1f94bda0-0000-0000-0000-000000000000")
    mqtt = MqttService(config, "1f94bda0-0000-0000-0000-000000000000")
    cfg_path = tmp_path / "nilo-node.yaml"
    cfg_path.write_text("camera:\n  device_ip: ''\n", encoding="utf-8")
    applier = SettingsApplier(config, cfg_path, camera=camera, wifi=wifi, bluetooth=bluetooth)
    app = create_app(
        "1f94bda0-0000-0000-0000-000000000000",
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
    client = TestClient(app)
    res = client.post(
        "/api/v1/setup/login",
        json={"username": "1f94bda0", "password": "nilo2026"},
    )
    assert res.status_code == 200


def test_setup_portal_static(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    res = client.get("/setup/")
    assert res.status_code == 200
    assert "NILO-Node" in res.text


@pytest.mark.asyncio
async def test_mqtt_mock_mode_and_handlers(tmp_path: Path) -> None:
    config = _minimal_config(tmp_path)
    camera = CameraManager(config)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    bluetooth = BluetoothManager(config, repo)
    wifi = WifiApManager(config, tmp_path, "node-abc-123")
    store = RuntimeSettingsStore(db, tmp_path)
    applier = SettingsApplier(config, tmp_path / "nilo-node.yaml", camera=camera, wifi=wifi, bluetooth=bluetooth)

    mqtt = MqttService(config, "node-abc-123")
    wire_mqtt_handlers(mqtt, camera=camera, bluetooth=bluetooth, wifi=wifi, settings_store=store, settings_applier=applier)
    await mqtt.start()

    status = mqtt.get_status()
    assert status.mock is True
    assert status.subscribe_topic == "nilo/node/node-abc-123"

    result = await mqtt._handlers["ping"]({})
    assert result == {"pong": True}

    raw = json.dumps({"action": "ping", "token": "secret-token", "request_id": "1"}).encode()
    await mqtt._dispatch(raw)

    await mqtt.stop()
    db.close()
