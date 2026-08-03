"""Chunk rotation and finalization."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ulid import ULID

from nilo_node.chunks.manifest import build_manifest
from nilo_node.monitoring.models import Campaign, ChunkRecord, RecordingRun
from nilo_node.sources.base import ChunkContext, DataSource, SourceManifest
from nilo_node.state.repository import StateRepository
from nilo_node.storage.paths import StoragePaths

logger = logging.getLogger(__name__)

OnChunkFinalized = Callable[[ChunkRecord], Awaitable[None]]


def _align_chunk_start(now: datetime, duration_sec: int) -> datetime:
    epoch = int(now.timestamp())
    aligned = epoch - (epoch % duration_sec)
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


class ChunkCoordinator:
    def __init__(
        self,
        repo: StateRepository,
        paths: StoragePaths,
        sources: list[DataSource],
        on_finalized: OnChunkFinalized | None = None,
    ) -> None:
        self._repo = repo
        self._paths = paths
        self._sources = sources
        self._on_finalized = on_finalized
        self._current_chunk_id: str | None = None
        self._current_chunk_path: Path | None = None
        self._current_chunk_start: datetime | None = None
        self._active_run: RecordingRun | None = None
        self._active_campaign: Campaign | None = None

    @property
    def active_chunk_id(self) -> str | None:
        return self._current_chunk_id

    async def set_active_run(
        self,
        run: RecordingRun | None,
        campaign: Campaign | None,
    ) -> None:
        if run is None and self._current_chunk_id is not None:
            await self._finalize_current_chunk(datetime.now(timezone.utc))
        self._active_run = run
        self._active_campaign = campaign

    async def tick(self, now: datetime | None = None) -> None:
        if self._active_run is None or self._active_campaign is None:
            return

        now = now or datetime.now(timezone.utc)
        duration = self._active_campaign.chunk_duration_sec
        aligned_start = _align_chunk_start(now, duration)

        if self._current_chunk_id is None:
            await self._open_chunk(aligned_start, duration)
            return

        assert self._current_chunk_start is not None
        if now >= self._current_chunk_start + timedelta(seconds=duration):
            await self._finalize_current_chunk(
                self._current_chunk_start + timedelta(seconds=duration)
            )
            await self._open_chunk(aligned_start, duration)

    async def _open_chunk(self, start: datetime, duration_sec: int) -> None:
        assert self._active_run is not None
        assert self._active_campaign is not None

        chunk_id = str(ULID.from_datetime(start))
        chunk_path = self._paths.chunk_dir(
            self._active_campaign.campaign_id,
            self._active_run.recording_run_id,
            chunk_id,
        )
        chunk_path.mkdir(parents=True, exist_ok=True)

        ctx = ChunkContext(
            chunk_id=chunk_id,
            chunk_path=chunk_path,
            campaign_id=self._active_campaign.campaign_id,
            campaign_name=self._active_campaign.campaign_name,
            recording_run_id=self._active_run.recording_run_id,
            subject_user_id=self._active_campaign.subject_user_id,
            node_id=self._active_run.node_id,
            start_ts=start,
            chunk_duration_sec=duration_sec,
        )

        for source in self._sources:
            await source.on_chunk_open(ctx)

        end = start + timedelta(seconds=duration_sec)
        record = ChunkRecord(
            chunk_id=chunk_id,
            campaign_id=self._active_campaign.campaign_id,
            campaign_name=self._active_campaign.campaign_name,
            recording_run_id=self._active_run.recording_run_id,
            subject_user_id=self._active_campaign.subject_user_id,
            node_id=self._active_run.node_id,
            start_ts=start,
            end_ts=end,
            path=str(chunk_path),
            status="open",
        )
        self._repo.insert_chunk(record)

        self._current_chunk_id = chunk_id
        self._current_chunk_path = chunk_path
        self._current_chunk_start = start
        logger.info("Opened chunk %s at %s", chunk_id, start.isoformat())

    async def _finalize_current_chunk(self, end: datetime) -> None:
        if self._current_chunk_id is None or self._current_chunk_path is None:
            return
        assert self._active_run is not None
        assert self._active_campaign is not None
        assert self._current_chunk_start is not None

        ctx = ChunkContext(
            chunk_id=self._current_chunk_id,
            chunk_path=self._current_chunk_path,
            campaign_id=self._active_campaign.campaign_id,
            campaign_name=self._active_campaign.campaign_name,
            recording_run_id=self._active_run.recording_run_id,
            subject_user_id=self._active_campaign.subject_user_id,
            node_id=self._active_run.node_id,
            start_ts=self._current_chunk_start,
            chunk_duration_sec=self._active_campaign.chunk_duration_sec,
        )

        source_manifests: dict[str, SourceManifest] = {}
        for source in self._sources:
            source_manifests[source.source_id] = await source.on_chunk_finalize(ctx)

        sources_payload = {
            sid: manifest.model_dump(mode="json")
            for sid, manifest in source_manifests.items()
        }
        manifest = build_manifest(
            chunk_id=self._current_chunk_id,
            campaign_id=self._active_campaign.campaign_id,
            campaign_name=self._active_campaign.campaign_name,
            recording_run_id=self._active_run.recording_run_id,
            subject_user_id=self._active_campaign.subject_user_id,
            node_id=self._active_run.node_id,
            start=self._current_chunk_start,
            end=end,
            chunk_duration_sec=self._active_campaign.chunk_duration_sec,
            sources=sources_payload,
        )

        manifest_path = self._current_chunk_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (self._current_chunk_path / ".complete").touch()

        byte_size = sum(
            f.stat().st_size for f in self._current_chunk_path.rglob("*") if f.is_file()
        )
        self._repo.update_chunk_status(
            self._current_chunk_id,
            "complete",
            sorted(sources_payload.keys()),
            byte_size,
        )

        record = ChunkRecord(
            chunk_id=self._current_chunk_id,
            campaign_id=self._active_campaign.campaign_id,
            campaign_name=self._active_campaign.campaign_name,
            recording_run_id=self._active_run.recording_run_id,
            subject_user_id=self._active_campaign.subject_user_id,
            node_id=self._active_run.node_id,
            start_ts=self._current_chunk_start,
            end_ts=end,
            path=str(self._current_chunk_path),
            status="complete",
            sources_present=sorted(sources_payload.keys()),
            byte_size=byte_size,
        )

        logger.info("Finalized chunk %s", self._current_chunk_id)
        self._current_chunk_id = None
        self._current_chunk_path = None
        self._current_chunk_start = None

        if self._on_finalized is not None:
            await self._on_finalized(record)

    async def resume_open_chunk(
        self,
        chunk: ChunkRecord,
        run: RecordingRun,
        campaign: Campaign,
    ) -> None:
        chunk_path = Path(chunk.path)
        ctx = ChunkContext(
            chunk_id=chunk.chunk_id,
            chunk_path=chunk_path,
            campaign_id=campaign.campaign_id,
            campaign_name=campaign.campaign_name,
            recording_run_id=run.recording_run_id,
            subject_user_id=campaign.subject_user_id,
            node_id=run.node_id,
            start_ts=chunk.start_ts,
            chunk_duration_sec=campaign.chunk_duration_sec,
        )

        for source in self._sources:
            await source.on_chunk_open(ctx)

        self._current_chunk_id = chunk.chunk_id
        self._current_chunk_path = chunk_path
        self._current_chunk_start = chunk.start_ts
        self._active_run = run
        self._active_campaign = campaign
        logger.info("Resumed open chunk %s", chunk.chunk_id)

    async def recover_partial_chunk(
        self,
        chunk: ChunkRecord,
        campaign: Campaign | None,
        run: RecordingRun | None,
    ) -> ChunkRecord:
        chunk_path = Path(chunk.path)
        end = min(datetime.now(timezone.utc), chunk.end_ts)

        campaign_name = campaign.campaign_name if campaign else chunk.campaign_name
        campaign_id = campaign.campaign_id if campaign else chunk.campaign_id
        subject_user_id = campaign.subject_user_id if campaign else chunk.subject_user_id
        chunk_duration_sec = campaign.chunk_duration_sec if campaign else int(
            (chunk.end_ts - chunk.start_ts).total_seconds()
        )
        node_id = run.node_id if run else chunk.node_id
        recording_run_id = run.recording_run_id if run else chunk.recording_run_id

        ctx = ChunkContext(
            chunk_id=chunk.chunk_id,
            chunk_path=chunk_path,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            recording_run_id=recording_run_id,
            subject_user_id=subject_user_id,
            node_id=node_id,
            start_ts=chunk.start_ts,
            chunk_duration_sec=chunk_duration_sec,
        )

        source_manifests: dict[str, SourceManifest] = {}
        for source in self._sources:
            (chunk_path / "sources" / source.source_id).mkdir(parents=True, exist_ok=True)
            source_manifests[source.source_id] = await source.on_chunk_finalize(ctx)

        sources_payload = {
            sid: manifest.model_dump(mode="json")
            for sid, manifest in source_manifests.items()
        }
        manifest = build_manifest(
            chunk_id=chunk.chunk_id,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            recording_run_id=recording_run_id,
            subject_user_id=subject_user_id,
            node_id=node_id,
            start=chunk.start_ts,
            end=end,
            chunk_duration_sec=chunk_duration_sec,
            sources=sources_payload,
        )
        manifest["recovered"] = True
        manifest["partial"] = True

        manifest_path = chunk_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (chunk_path / ".complete").touch()

        byte_size = sum(f.stat().st_size for f in chunk_path.rglob("*") if f.is_file())
        sources_present = sorted(sources_payload.keys())
        self._repo.update_chunk_status(
            chunk.chunk_id,
            "complete",
            sources_present,
            byte_size,
        )

        record = ChunkRecord(
            chunk_id=chunk.chunk_id,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            recording_run_id=recording_run_id,
            subject_user_id=subject_user_id,
            node_id=node_id,
            start_ts=chunk.start_ts,
            end_ts=end,
            path=str(chunk_path),
            status="complete",
            sources_present=sources_present,
            byte_size=byte_size,
        )

        if self._on_finalized is not None:
            await self._on_finalized(record)

        return record
