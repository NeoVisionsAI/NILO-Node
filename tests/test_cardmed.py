"""Tests for Cardmed API, physiology ingest, and WiFi AP manager."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
from nilo_node.monitoring.models import AlwaysSchedule, Campaign, CampaignStatus, ChunkRecord
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.sources.physiology.store import PhysiologyStore
from nilo_node.sources.registry import build_sources, init_camera_manager
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.paths import StoragePaths
from nilo_node.storage.replication.manager import ReplicationManager, enabled_replication_target_ids


@contextmanager
def _build_test_app(tmp_path: Path, token: str = "test-token") -> Iterator[tuple[TestClient, StateRepository]]:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path), "retention_check_interval_sec": 99999},
            "local_api": {"auth_token": token},
            "backend": {"enabled": False},
            "wifi": {"enabled": True, "mock_when_unavailable": True},
            "bluetooth": {"enabled": True, "mock_when_unavailable": True, "adapter": "hci99"},
            "cardmed": {"forward_to_backend": False},
            "sources": {
                "rgb": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "tof": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "pose": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "audio": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "physiology": {
                    "enabled": True,
                    "plugin": "nilo_node.sources.physiology.source.PhysiologySource",
                },
            },
            "monitoring": {"default_chunk_duration_sec": 300, "schedule_tick_sec": 1},
        }
    )
    paths = StoragePaths(tmp_path, config.storage.recordings_dir)
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    camera = CameraManager(config)
    init_camera_manager(camera)
    physiology_store = PhysiologyStore(config.cardmed, repo)
    bluetooth = BluetoothManager(config, repo)
    sources = build_sources(config, physiology_store=physiology_store)
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
    wifi = WifiApManager(config, tmp_path, "node-test-uuid-1234")

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
        yield client, repo
    db.close()


@contextmanager
def _cardmed_client(tmp_path: Path) -> Iterator[tuple[TestClient, StateRepository]]:
    with _build_test_app(tmp_path) as client_repo:
        yield client_repo


@pytest.mark.asyncio
async def test_wifi_ap_mock_mode(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "wifi": {"enabled": True, "mock_when_unavailable": True},
        }
    )
    wifi = WifiApManager(config, tmp_path, "abcd1234-5678-90ab-cdef-1234567890ab")
    await wifi.start()
    status = wifi.get_status()
    assert status.enabled is True
    assert status.running is True
    assert status.mock is True
    assert status.ssid == "nilo-node-abcd1234"
    await wifi.stop()


def test_wifi_dnsmasq_config_syntax(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "wifi": {"enabled": True, "interface": "wlp3s0"},
        }
    )
    wifi = WifiApManager(config, tmp_path, "abcd1234-5678-90ab-cdef-1234567890ab")
    wifi._active_interface = "wlp3s0"
    (tmp_path / "wifi").mkdir()
    wifi._write_dnsmasq_config()
    text = (tmp_path / "wifi" / "dnsmasq.conf").read_text(encoding="utf-8")
    assert "255.255.255.0" not in text
    assert "address=/" not in text
    assert "port=0" in text
    assert "dhcp-authoritative" in text
    assert "dhcp-option=option:router,192.168.50.1" in text


def test_cardmed_register_and_upload(tmp_path: Path) -> None:
    with _build_test_app(tmp_path) as (client, repo):
        headers = {"Authorization": "Bearer test-token"}

        start = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        chunk_path = (
            tmp_path
            / "recordings"
            / "campaigns"
            / "c1"
            / "runs"
            / "r1"
            / "chunks"
            / "ch1"
        )
        chunk_path.mkdir(parents=True)
        (chunk_path / "sources" / "physiology" / "images").mkdir(parents=True)
        repo.insert_chunk(
            ChunkRecord(
                chunk_id="ch1",
                campaign_id="c1",
                campaign_name="test",
                recording_run_id="r1",
                subject_user_id=None,
                node_id="node-test",
                start_ts=start,
                end_ts=start + timedelta(seconds=300),
                path=str(chunk_path),
                status="open",
            )
        )

        reg = client.post(
            "/api/v1/cardmed/register",
            headers=headers,
            json={"device_id": "cardmed-1", "device_name": "Dev Kit"},
        )
        assert reg.status_code == 200
        assert reg.json()["device_id"] == "cardmed-1"

        capture_ts = (start + timedelta(seconds=30)).isoformat()
        image_bytes = b"\xff\xd8\xff\xd9"
        assert repo.get_open_chunk() is not None
        response = client.post(
            "/api/v1/cardmed/photos",
            headers=headers,
            data={"device_id": "cardmed-1", "capture_ts": capture_ts},
            files={"file": ("photo.jpg", io.BytesIO(image_bytes), "image/jpeg")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["chunk_id"] == "ch1"
        assert body["late"] is False

        index_path = chunk_path / "sources" / "physiology" / "index.jsonl"
        assert index_path.exists()
        line = index_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        assert entry["device_id"] == "cardmed-1"
        assert (chunk_path / entry["image"]).exists()


def test_cardmed_upload_requires_registration(tmp_path: Path) -> None:
    with _build_test_app(tmp_path) as (client, repo):
        headers = {"Authorization": "Bearer test-token"}

        start = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        chunk_path = tmp_path / "chunk"
        chunk_path.mkdir()
        repo.insert_chunk(
            ChunkRecord(
                chunk_id="ch1",
                campaign_id="c1",
                campaign_name="test",
                recording_run_id="r1",
                subject_user_id=None,
                node_id="node-test",
                start_ts=start,
                end_ts=start + timedelta(seconds=300),
                path=str(chunk_path),
                status="open",
            )
        )

        response = client.post(
            "/api/v1/cardmed/photos",
            headers=headers,
            data={
                "device_id": "unknown",
                "capture_ts": start.isoformat(),
            },
            files={"file": ("photo.jpg", io.BytesIO(b"\xff\xd8\xff\xd9"), "image/jpeg")},
        )
        assert response.status_code == 403


def test_devices_endpoint(tmp_path: Path) -> None:
    with _build_test_app(tmp_path) as (client, _):
        headers = {"Authorization": "Bearer test-token"}
        response = client.get("/api/v1/devices", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert "camera" in body
        assert "cardmed" in body
        assert "wifi" in body
        assert "bluetooth" in body
