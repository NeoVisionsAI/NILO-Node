"""Startup recovery for open chunks after crash or restart."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.controller import CampaignController
from nilo_node.monitoring.models import Campaign
from nilo_node.state.repository import StateRepository
from nilo_node.storage.models import ChunkQuery

logger = logging.getLogger(__name__)


class ChunkRecoveryService:
    def __init__(
        self,
        config: AppConfig,
        repo: StateRepository,
        coordinator: ChunkCoordinator,
        campaign_controller: CampaignController,
    ) -> None:
        self._config = config
        self._repo = repo
        self._coordinator = coordinator
        self._campaign = campaign_controller

    async def run_startup_recovery(self) -> dict[str, list[str]]:
        report: dict[str, list[str]] = {
            "finalized": [],
            "resumed": [],
            "aborted": [],
        }
        if not self._config.monitoring.recover_on_startup:
            return report

        now = datetime.now(timezone.utc)
        open_chunks = self._repo.list_chunks(ChunkQuery(status="open", limit=100))

        for chunk in open_chunks:
            chunk_path = Path(chunk.path)
            if not chunk_path.exists():
                self._repo.update_chunk_status(chunk.chunk_id, "aborted", [], 0)
                report["aborted"].append(chunk.chunk_id)
                logger.warning("Aborted missing open chunk %s", chunk.chunk_id)
                continue

            run = self._repo.get_recording_run(chunk.recording_run_id)
            campaign = self._repo.get_campaign(chunk.campaign_id)

            if (
                run is not None
                and run.end_ts is None
                and now < chunk.end_ts
                and campaign is not None
                and campaign.status.value in ("active", "paused", "scheduled")
            ):
                await self._coordinator.resume_open_chunk(chunk, run, campaign)
                await self._campaign.restore_active_capture(campaign, run)
                report["resumed"].append(chunk.chunk_id)
                logger.info("Resumed open chunk %s", chunk.chunk_id)
                continue

            await self._coordinator.recover_partial_chunk(chunk, campaign, run)
            report["finalized"].append(chunk.chunk_id)
            logger.info("Recovered partial chunk %s", chunk.chunk_id)

        return report
