"""Replicate chunks to NILO-backend via manifest and upload adapters."""

from __future__ import annotations

import logging
from pathlib import Path

from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import ChunkRecord

logger = logging.getLogger(__name__)


class BackendUploadTarget:
    target_id = "backend"

    def __init__(
        self,
        config: AppConfig,
        backend: BackendClient,
        upload_queue: UploadQueueService | None = None,
    ) -> None:
        self._config = config
        self._backend = backend
        self._upload_queue = upload_queue

    def _sync_configured(self) -> bool:
        manifest = (
            self._config.adapter_enabled("manifest")
            and bool(self._config.backend.endpoints.manifest)
        )
        upload = (
            self._config.adapter_enabled("upload")
            and bool(self._config.backend.endpoints.upload)
        )
        return manifest or upload

    async def replicate(self, chunk: ChunkRecord, chunk_path: Path) -> None:
        if not self._sync_configured():
            logger.info(
                "Backend replication stub: chunk %s ready (manifest/upload not configured)",
                chunk.chunk_id,
            )
            return

        try:
            await self._backend.sync_chunk(chunk, chunk_path)
            logger.info("Backend sync complete: chunk=%s", chunk.chunk_id)
        except Exception as exc:
            logger.warning(
                "Backend sync failed chunk=%s — enqueueing for offline retry: %s",
                chunk.chunk_id,
                exc,
            )
            if self._upload_queue is not None:
                self._upload_queue.enqueue_chunk_sync(chunk, chunk_path)
            raise
