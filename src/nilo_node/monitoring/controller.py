"""Campaign and recording-run lifecycle management."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID

from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.monitoring.models import Campaign, CampaignStatus, RecordingRun
from nilo_node.monitoring.schedule import is_capture_active
from nilo_node.sources.base import DataSource
from nilo_node.state.repository import StateRepository
from nilo_node.storage.paths import StoragePaths

logger = logging.getLogger(__name__)


class CampaignController:
    def __init__(
        self,
        repo: StateRepository,
        chunk_coordinator: ChunkCoordinator,
        sources: list[DataSource],
        paths: StoragePaths,
        node_id: str,
    ) -> None:
        self._repo = repo
        self._chunks = chunk_coordinator
        self._sources = sources
        self._paths = paths
        self._node_id = node_id
        self._campaign: Campaign | None = None
        self._run: RecordingRun | None = None
        self._capture_active = False

    @property
    def campaign(self) -> Campaign | None:
        return self._campaign

    @property
    def recording_run(self) -> RecordingRun | None:
        return self._run

    @property
    def capture_active(self) -> bool:
        return self._capture_active

    def set_campaign(self, campaign: Campaign | None) -> None:
        self._campaign = campaign
        if campaign is not None:
            self._repo.upsert_campaign(campaign)
            campaign_dir = self._paths.campaign_dir(campaign.campaign_id)
            campaign_dir.mkdir(parents=True, exist_ok=True)
            campaign_json = campaign.model_dump(mode="json")
            (campaign_dir / "campaign.json").write_text(
                json.dumps(campaign_json, indent=2),
                encoding="utf-8",
            )

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        should_capture = is_capture_active(self._campaign, now)

        if should_capture and not self._capture_active:
            await self._start_capture(now)
        elif not should_capture and self._capture_active:
            await self._stop_capture(now)

        if self._capture_active:
            await self._chunks.tick(now)

    async def _start_capture(self, now: datetime) -> None:
        if self._campaign is None:
            return

        for source in self._sources:
            await source.on_campaign_start(self._campaign)

        existing = self._repo.get_open_recording_run(self._campaign.campaign_id)
        if existing is not None:
            self._run = existing
        else:
            run_id = str(ULID.from_datetime(now))
            run_path = self._paths.run_dir(self._campaign.campaign_id, run_id)
            run_path.mkdir(parents=True, exist_ok=True)
            self._run = RecordingRun(
                recording_run_id=run_id,
                campaign_id=self._campaign.campaign_id,
                campaign_name=self._campaign.campaign_name,
                subject_user_id=self._campaign.subject_user_id,
                node_id=self._node_id,
                start_ts=now,
                path=str(run_path),
            )
            self._repo.create_recording_run(self._run)

        for source in self._sources:
            await source.on_run_start(self._run)

        await self._chunks.set_active_run(self._run, self._campaign)
        self._capture_active = True
        logger.info(
            "Capture started: campaign=%s run=%s subject_user_id=%s",
            self._campaign.campaign_name,
            self._run.recording_run_id,
            self._campaign.subject_user_id,
        )

    async def _stop_capture(self, now: datetime) -> None:
        await self._chunks.set_active_run(None, None)

        if self._run is not None:
            for source in self._sources:
                await source.on_run_stop(self._run)
            self._repo.close_recording_run(self._run.recording_run_id, now)

        if self._campaign is not None and self._campaign.status == CampaignStatus.CANCELLED:
            for source in self._sources:
                await source.on_campaign_stop(self._campaign)

        logger.info("Capture stopped at %s", now.isoformat())
        self._capture_active = False
        self._run = None

    async def restore_active_capture(
        self,
        campaign: Campaign,
        run: RecordingRun,
    ) -> None:
        self._campaign = campaign
        self._run = run
        self._capture_active = True
        logger.info(
            "Restored active capture: campaign=%s run=%s",
            campaign.campaign_name,
            run.recording_run_id,
        )
