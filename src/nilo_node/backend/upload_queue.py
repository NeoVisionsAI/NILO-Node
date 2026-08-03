"""Offline upload queue for manifest and chunk sync jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.state.repository import StateRepository

if TYPE_CHECKING:
    from nilo_node.backend.client import BackendClient

logger = logging.getLogger(__name__)


class UploadQueueService:
    """Persists and retries backend sync when the node is offline."""

    def __init__(self, config: AppConfig, repo: StateRepository) -> None:
        self._config = config
        self._repo = repo
        self._task: asyncio.Task[None] | None = None

    def start(self, backend: BackendClient) -> None:
        if not self._config.backend.upload_queue.enabled:
            return
        self._task = asyncio.create_task(self._loop(backend))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enqueue_chunk_sync(self, chunk: ChunkRecord, chunk_path: Path) -> None:
        if not self._config.backend.upload_queue.enabled:
            return

        if self._manifest_enabled():
            self._repo.insert_upload_job(chunk.chunk_id, "manifest")
        if self._upload_enabled():
            self._repo.insert_upload_job(chunk.chunk_id, "upload")

        logger.info("Enqueued backend sync jobs for chunk %s", chunk.chunk_id)

    async def process_pending(self, backend: BackendClient) -> int:
        if not self._config.backend.upload_queue.enabled:
            return 0

        jobs = self._repo.fetch_pending_upload_jobs()
        processed = 0
        max_attempts = self._config.backend.upload_queue.max_attempts

        for job in jobs:
            if job["attempts"] >= max_attempts:
                continue

            chunk = self._repo.get_chunk(job["chunk_id"])
            if chunk is None:
                self._repo.update_upload_job(
                    job["job_id"],
                    "failed",
                    last_error="chunk not found",
                    increment_attempts=True,
                )
                continue

            chunk_path = Path(chunk.path)
            self._repo.update_upload_job(job["job_id"], "in_progress")
            try:
                if job["job_type"] == "manifest":
                    await backend.send_chunk_manifest(chunk, chunk_path)
                elif job["job_type"] == "upload":
                    await backend.upload_chunk_files(chunk, chunk_path)
                else:
                    raise ValueError(f"unknown upload job type: {job['job_type']}")

                self._repo.update_upload_job(job["job_id"], "complete")
                processed += 1
            except Exception as exc:
                logger.warning(
                    "Upload queue job failed job=%s chunk=%s type=%s: %s",
                    job["job_id"],
                    job["chunk_id"],
                    job["job_type"],
                    exc,
                )
                self._repo.update_upload_job(
                    job["job_id"],
                    "pending",
                    last_error=str(exc),
                    increment_attempts=True,
                )

        return processed

    def stats(self) -> dict[str, int]:
        return self._repo.upload_queue_stats()

    async def _loop(self, backend: BackendClient) -> None:
        interval = self._config.backend.upload_queue.process_interval_sec
        while True:
            try:
                processed = await self.process_pending(backend)
                if processed:
                    logger.info("Upload queue processed %d job(s)", processed)
            except Exception as exc:
                logger.warning("Upload queue loop error: %s", exc)
            await asyncio.sleep(interval)

    def _manifest_enabled(self) -> bool:
        return (
            self._config.adapter_enabled("manifest")
            and bool(self._config.backend.endpoints.manifest)
        )

    def _upload_enabled(self) -> bool:
        return (
            self._config.adapter_enabled("upload")
            and bool(self._config.backend.endpoints.upload)
        )
