"""Tests for camera manager and capture flags."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nilo_node.camera.manager import CameraManager
from nilo_node.camera.models import CaptureFlags
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import AlwaysSchedule, Campaign, CampaignStatus
from nilo_node.sources.base import ChunkContext
from nilo_node.sources.rgb.source import RgbSource
from nilo_node.sources.registry import init_camera_manager


@pytest.mark.asyncio
async def test_connect_mock_when_no_hardware(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "camera": {"mock_when_unavailable": True, "auto_connect": False},
        }
    )
    camera = CameraManager(config)
    status = await camera.connect(None)
    assert status.connection_state.value == "connected"
    assert status.pipeline_mode == "mock"


def test_capture_flags_from_backend_campaign() -> None:
    flags = CaptureFlags.from_campaign_sources(
        {
            "rgb": {"enabled": True, "record_video": True},
            "tof": {"enabled": False},
            "pose": {"enabled": True, "record_landmarks": False},
        }
    )
    assert flags.rgb is True
    assert flags.tof is False
    assert flags.pose is False


@pytest.mark.asyncio
async def test_chunk_capture_respects_disabled_streams(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "storage": {"base_path": str(tmp_path)},
            "camera": {"mock_when_unavailable": True},
        }
    )
    camera = CameraManager(config)
    init_camera_manager(camera)

    campaign = Campaign(
        campaign_id="c1",
        campaign_name="test",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
        sources={
            "rgb": {"enabled": True},
            "tof": {"enabled": False},
            "pose": {"enabled": False},
        },
    )
    camera.set_campaign(campaign)
    await camera.connect(None)

    chunk_path = tmp_path / "recordings" / "campaigns" / "c1" / "runs" / "r1" / "chunks" / "ch1"
    chunk_path.mkdir(parents=True)

    await camera.begin_chunk("ch1", chunk_path)
    rgb_manifest = await camera.finalize_source("ch1", "rgb")
    tof_manifest = await camera.finalize_source("ch1", "tof")

    assert rgb_manifest is not None
    assert (chunk_path / "sources" / "rgb" / "video.mp4").exists()
    assert tof_manifest is None
    assert not (chunk_path / "sources" / "tof").exists()


@pytest.mark.asyncio
async def test_rgb_source_writes_mock_chunk(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {"storage": {"base_path": str(tmp_path)}, "camera": {"mock_when_unavailable": True}}
    )
    camera = CameraManager(config)
    init_camera_manager(camera)
    await camera.connect(None)

    source = RgbSource("rgb", camera)
    chunk_path = tmp_path / "chunk"
    chunk_path.mkdir()
    ctx = ChunkContext(
        chunk_id="ch1",
        chunk_path=chunk_path,
        campaign_id="c1",
        campaign_name="t",
        recording_run_id="r1",
        subject_user_id=None,
        node_id="n1",
        start_ts=datetime.now(timezone.utc),
        chunk_duration_sec=60,
    )
    await source.on_chunk_open(ctx)
    import asyncio

    await asyncio.sleep(0.05)
    manifest = await source.on_chunk_finalize(ctx)
    assert manifest.extra.get("mock") is True
