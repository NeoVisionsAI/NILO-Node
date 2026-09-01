"""Tests for Bluetooth API, manager, and audio chunk capture."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from nilo_node.api.app import create_app
from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.controller import CampaignController
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.sources.audio.source import AudioSource
from nilo_node.sources.base import ChunkContext
from nilo_node.sources.physiology.store import PhysiologyStore
from nilo_node.sources.registry import build_sources, init_bluetooth_manager, init_camera_manager
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.paths import StoragePaths
from nilo_node.storage.replication.manager import ReplicationManager, enabled_replication_target_ids


@contextmanager
def _test_client(tmp_path: Path) -> Iterator[tuple[TestClient, BluetoothManager]]:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path), "retention_check_interval_sec": 99999},
            "local_api": {"auth_token": "test-token"},
            "backend": {"enabled": False},
            "bluetooth": {
                "enabled": True,
                "mock_when_unavailable": True,
                "adapter": "hci99",
            },
            "wifi": {"enabled": False},
            "sources": {
                "rgb": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "tof": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "pose": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "audio": {
                    "enabled": True,
                    "plugin": "nilo_node.sources.audio.source.AudioSource",
                },
                "physiology": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
            },
        }
    )
    paths = StoragePaths(tmp_path, config.storage.recordings_dir)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    camera = CameraManager(config)
    init_camera_manager(camera)
    bluetooth = BluetoothManager(config, repo)
    asyncio.run(bluetooth.start())
    init_bluetooth_manager(bluetooth)

    physiology_store = PhysiologyStore(config.cardmed, repo)
    sources = build_sources(
        config,
        physiology_store=physiology_store,
        bluetooth_manager=bluetooth,
    )
    backend = BackendClient(config, tmp_path, "node-test")
    upload_queue = UploadQueueService(config, repo)
    chunk_coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(repo, chunk_coordinator, sources, paths, "node-test")
    storage_manager = StorageManager(
        config, repo, paths, enabled_target_ids=enabled_replication_target_ids(config)
    )
    replication_manager = ReplicationManager(
        config, repo, paths, backend, storage_manager, upload_queue
    )
    cardmed = CardmedService(config, repo, "node-test", physiology_store, backend)
    wifi = WifiApManager(config, tmp_path, "node-test")

    app = create_app(
        "node-test",
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
    )
    with TestClient(app) as client:
        yield client, bluetooth
    db.close()


@pytest.mark.asyncio
async def test_bluetooth_mock_start(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "bluetooth": {"mock_when_unavailable": True, "adapter": "hci99"},
        }
    )
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    bluetooth = BluetoothManager(config, repo)
    await bluetooth.start()
    status = bluetooth.get_status()
    assert status.mock is True
    assert status.adapter_state.value == "mock"


def test_bluetooth_discover_connect_disconnect(tmp_path: Path) -> None:
    with _test_client(tmp_path) as (client, _):
        headers = {"Authorization": "Bearer test-token"}

        discover = client.get("/api/v1/bluetooth/discover", headers=headers)
        assert discover.status_code == 200
        devices = discover.json()["devices"]
        assert len(devices) >= 1
        mac = devices[0]["mac_address"]

        connect = client.post(
            "/api/v1/bluetooth/connect",
            headers=headers,
            json={"mac_address": mac, "device_name": "Test Mic"},
        )
        assert connect.status_code == 200
        assert connect.json()["connected"] is True
        assert connect.json()["record_enabled"] is False

        enable_rec = client.patch(
            f"/api/v1/bluetooth/mics/{mac}/recording",
            headers=headers,
            json={"record_enabled": True},
        )
        assert enable_rec.status_code == 200
        assert enable_rec.json()["record_enabled"] is True

        status = client.get("/api/v1/bluetooth/status", headers=headers)
        assert status.status_code == 200
        assert status.json()["connected_count"] == 1

        recording_off = client.patch(
            f"/api/v1/bluetooth/mics/{mac}/recording",
            headers=headers,
            json={"record_enabled": False},
        )
        assert recording_off.status_code == 200
        assert recording_off.json()["record_enabled"] is False

        disconnect = client.post(
            "/api/v1/bluetooth/disconnect",
            headers=headers,
            json={"mac_address": mac},
        )
        assert disconnect.status_code == 200
        assert disconnect.json()["connected"] is False


def test_bluetooth_mic_settings_and_test_recording(tmp_path: Path) -> None:
    with _test_client(tmp_path) as (client, _):
        headers = {"Authorization": "Bearer test-token"}
        mac = "AA:BB:CC:DD:EE:01"
        client.post(
            "/api/v1/bluetooth/connect",
            headers=headers,
            json={"mac_address": mac, "device_name": "Mic Lab"},
        )
        updated = client.patch(
            f"/api/v1/bluetooth/mics/{mac}",
            headers=headers,
            json={
                "display_name": "Mic principal",
                "recording_mode": "on_demand",
                "record_enabled": True,
                "recording_active": False,
            },
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["display_name"] == "Mic principal"
        assert body["label"] == "Mic principal"
        assert body["recording_mode"] == "on_demand"
        assert body["record_enabled"] is True
        assert body["recording_active"] is False

        test_rec = client.post(
            f"/api/v1/bluetooth/mics/{mac}/test-recording",
            headers=headers,
            json={"duration_sec": 1.0},
        )
        assert test_rec.status_code == 200
        playback_url = test_rec.json()["playback_url"]
        audio = client.get(playback_url, headers=headers)
        assert audio.status_code == 200
        assert audio.headers["content-type"].startswith("audio/")

        unpair = client.post(
            "/api/v1/bluetooth/unpair",
            headers=headers,
            json={"mac_address": mac},
        )
        assert unpair.status_code == 200
        status = client.get("/api/v1/bluetooth/status", headers=headers)
        assert status.json()["connected_count"] == 0


def test_recording_toggle_requires_known_mic(tmp_path: Path) -> None:
    with _test_client(tmp_path) as (client, _):
        headers = {"Authorization": "Bearer test-token"}
        response = client.patch(
            "/api/v1/bluetooth/mics/AA:BB:CC:DD:EE:99/recording",
            headers=headers,
            json={"record_enabled": True},
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_audio_source_writes_tracks_for_recording_mics(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "bluetooth": {
                "enabled": True,
                "mock_when_unavailable": True,
                "adapter": "hci99",
            },
        }
    )
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    bluetooth = BluetoothManager(config, repo)
    await bluetooth.start()

    await bluetooth.connect("AA:BB:CC:DD:EE:01", "Mic 1")
    bluetooth.update_mic_settings(
        "AA:BB:CC:DD:EE:01",
        record_enabled=True,
        recording_mode="continuous",
    )
    await bluetooth.connect("AA:BB:CC:DD:EE:02", "Mic 2")
    bluetooth.set_recording("AA:BB:CC:DD:EE:02", False)

    source = AudioSource("audio", bluetooth)
    chunk_path = tmp_path / "chunk"
    chunk_path.mkdir()
    ctx = ChunkContext(
        chunk_id="ch1",
        chunk_path=chunk_path,
        campaign_id="c1",
        campaign_name="test",
        recording_run_id="r1",
        subject_user_id=None,
        node_id="n1",
        start_ts=datetime.now(timezone.utc),
        chunk_duration_sec=60,
    )
    await source.on_chunk_open(ctx)
    await asyncio.sleep(0.15)
    manifest = await source.on_chunk_finalize(ctx)

    tracks = manifest.extra.get("tracks", [])
    assert len(tracks) == 1
    assert tracks[0]["mic_id"] == "bt:AA:BB:CC:DD:EE:01"
    assert (chunk_path / "sources" / "audio" / "bt_AABBCCDDEE01.flac").exists()
    assert (chunk_path / "sources" / "audio" / "bt_AABBCCDDEE01.timestamps.npy").exists()
