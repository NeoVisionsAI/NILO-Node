"""Chunk replication orchestration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ulid import ULID

from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.replication.backend_target import BackendUploadTarget
from nilo_node.storage.replication.nas_target import NasMirrorTarget
from nilo_node.storage.paths import StoragePaths

if TYPE_CHECKING:
    from nilo_node.backend.client import BackendClient
    from nilo_node.backend.upload_queue import UploadQueueService

logger = logging.getLogger(__name__)


def enabled_replication_target_ids(config: AppConfig) -> list[str]:
    ids: list[str] = []
    if config.replication.targets.backend.enabled:
        ids.append("backend")
    if config.replication.targets.nas.enabled:
        ids.append("nas")
    return ids


class ReplicationManager:
    def __init__(
        self,
        config: AppConfig,
        repo: StateRepository,
        paths: StoragePaths,
        backend: BackendClient,
        storage_manager: StorageManager,
        upload_queue: UploadQueueService | None = None,
    ) -> None:
        self._config = config
        self._repo = repo
        self._paths = paths
        self._storage = storage_manager
        self._upload_queue = upload_queue
        self._targets = self._build_targets(config, paths, backend, upload_queue)
        self._task: asyncio.Task[None] | None = None
        self._last_scheduled_run: str | None = None

    @property
    def enabled_target_ids(self) -> list[str]:
        return [t.target_id for t in self._targets]

    def _build_targets(
        self,
        config: AppConfig,
        paths: StoragePaths,
        backend: BackendClient,
        upload_queue: UploadQueueService | None,
    ) -> list:
        targets = []
        if config.replication.targets.backend.enabled:
            targets.append(BackendUploadTarget(config, backend, upload_queue))
        if config.replication.targets.nas.enabled:
            targets.append(NasMirrorTarget(config.replication.targets.nas, paths))
        return targets

    def start(self) -> None:
        if not self._config.replication.enabled or not self._targets:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def on_chunk_finalized(self, chunk: ChunkRecord) -> None:
        if not self._config.replication.enabled or not self._targets:
            return

        for target in self._targets:
            job_id = str(ULID())
            self._repo.insert_replication_job(job_id, chunk.chunk_id, target.target_id)

        if self._config.replication.mode == "realtime":
            await self.process_pending()

    async def process_pending(self) -> int:
        if not self._targets:
            return 0

        jobs = self._repo.fetch_pending_replication_jobs()
        processed = 0
        target_map = {t.target_id: t for t in self._targets}

        for job in jobs:
            if job["attempts"] >= self._config.replication.max_attempts:
                continue

            chunk = self._repo.get_chunk(job["chunk_id"])
            if chunk is None:
                self._repo.update_replication_job(
                    job["job_id"], "failed", last_error="chunk not found", increment_attempts=True
                )
                continue

            target = target_map.get(job["target_id"])
            if target is None:
                continue

            self._repo.update_replication_job(job["job_id"], "in_progress")
            try:
                await target.replicate(chunk, Path(chunk.path))
                now = datetime.now(timezone.utc).isoformat()
                self._repo.update_replication_job(job["job_id"], "complete")
                self._repo.upsert_chunk_replication(
                    chunk.chunk_id, target.target_id, "complete", now
                )
                processed += 1

                if self._config.replication.delete_local_after_replicated:
                    if self._repo.chunk_fully_replicated(
                        chunk.chunk_id, self.enabled_target_ids
                    ):
                        self._storage.delete_chunk(chunk.chunk_id)
            except Exception as exc:
                logger.warning(
                    "Replication failed chunk=%s target=%s: %s",
                    chunk.chunk_id,
                    job["target_id"],
                    exc,
                )
                self._repo.update_replication_job(
                    job["job_id"],
                    "failed",
                    last_error=str(exc),
                    increment_attempts=True,
                )

        return processed

    async def _loop(self) -> None:
        interval = self._config.replication.process_interval_sec
        while True:
            mode = self._config.replication.mode
            if mode == "realtime":
                await self.process_pending()
            elif mode == "scheduled" and self._should_run_scheduled():
                await self.process_pending()
            await asyncio.sleep(interval)

    def _should_run_scheduled(self) -> bool:
        tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        hour, minute = self._config.replication.daily_at.split(":")
        slot = f"{now.date().isoformat()}T{hour}:{minute}"
        if self._last_scheduled_run == slot:
            return False
        if now.hour == int(hour) and now.minute == int(minute):
            self._last_scheduled_run = slot
            return True
        return False
