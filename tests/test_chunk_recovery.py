"""Tests for partial chunk recovery on startup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.chunks.recovery import ChunkRecoveryService
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.controller import CampaignController
from nilo_node.monitoring.models import AlwaysSchedule, Campaign, CampaignStatus, ChunkRecord, RecordingRun
from nilo_node.sources.registry import build_sources
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.paths import StoragePaths


@pytest.mark.asyncio
async def test_recovery_finalizes_stale_open_chunk(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "monitoring": {"recover_on_startup": True, "default_chunk_duration_sec": 300},
            "sources": {
                "rgb": {"enabled": True, "plugin": "nilo_node.sources.stub.StubSource"},
                "tof": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "pose": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "audio": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "physiology": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
            },
        }
    )

    paths = StoragePaths(tmp_path, "recordings")
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    campaign = Campaign(
        campaign_id="camp-recover",
        campaign_name="recovery_test",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
    )
    repo.upsert_campaign(campaign)

    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    end = start + timedelta(seconds=300)
    run = RecordingRun(
        recording_run_id="run-recover",
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        subject_user_id=None,
        node_id="node-1",
        start_ts=start,
        end_ts=start + timedelta(minutes=1),
        path=str(paths.run_dir(campaign.campaign_id, "run-recover")),
    )
    repo.create_recording_run(run)

    chunk_path = paths.chunk_dir(campaign.campaign_id, run.recording_run_id, "chunk-open")
    chunk_path.mkdir(parents=True)

    chunk = ChunkRecord(
        chunk_id="chunk-open",
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        recording_run_id=run.recording_run_id,
        subject_user_id=None,
        node_id="node-1",
        start_ts=start,
        end_ts=end,
        path=str(chunk_path),
        status="open",
    )
    repo.insert_chunk(chunk)

    sources = build_sources(config)
    coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(repo, coordinator, sources, paths, "node-1")
    recovery = ChunkRecoveryService(config, repo, coordinator, controller)

    report = await recovery.run_startup_recovery()

    assert chunk.chunk_id in report["finalized"]
    recovered = repo.get_chunk("chunk-open")
    assert recovered is not None
    assert recovered.status == "complete"
    assert (chunk_path / "manifest.json").exists()
    manifest_text = (chunk_path / "manifest.json").read_text(encoding="utf-8")
    assert '"partial": true' in manifest_text
    assert '"recovered": true' in manifest_text
    db.close()


@pytest.mark.asyncio
async def test_recovery_resumes_active_open_chunk(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "monitoring": {"recover_on_startup": True, "default_chunk_duration_sec": 300},
            "sources": {
                "rgb": {"enabled": True, "plugin": "nilo_node.sources.stub.StubSource"},
                "tof": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "pose": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "audio": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
                "physiology": {"enabled": False, "plugin": "nilo_node.sources.stub.StubSource"},
            },
        }
    )

    paths = StoragePaths(tmp_path, "recordings")
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    campaign = Campaign(
        campaign_id="camp-resume",
        campaign_name="resume_test",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
    )
    repo.upsert_campaign(campaign)

    start = datetime.now(timezone.utc)
    end = start + timedelta(seconds=300)
    run = RecordingRun(
        recording_run_id="run-resume",
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        subject_user_id=None,
        node_id="node-1",
        start_ts=start,
        path=str(paths.run_dir(campaign.campaign_id, "run-resume")),
    )
    repo.create_recording_run(run)

    chunk_path = paths.chunk_dir(campaign.campaign_id, run.recording_run_id, "chunk-resume")
    chunk_path.mkdir(parents=True)

    chunk = ChunkRecord(
        chunk_id="chunk-resume",
        campaign_id=campaign.campaign_id,
        campaign_name=campaign.campaign_name,
        recording_run_id=run.recording_run_id,
        subject_user_id=None,
        node_id="node-1",
        start_ts=start,
        end_ts=end,
        path=str(chunk_path),
        status="open",
    )
    repo.insert_chunk(chunk)

    sources = build_sources(config)
    coordinator = ChunkCoordinator(repo, paths, sources)
    controller = CampaignController(repo, coordinator, sources, paths, "node-1")
    recovery = ChunkRecoveryService(config, repo, coordinator, controller)

    report = await recovery.run_startup_recovery()

    assert chunk.chunk_id in report["resumed"]
    assert coordinator.active_chunk_id == "chunk-resume"
    assert controller.capture_active is True
    db.close()
