"""Storage usage, retention, and chunk deletion."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nilo_node.config.models import AppConfig
from nilo_node.storage.models import ChunkQuery
from nilo_node.storage.paths import StoragePaths
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)


@dataclass
class DeleteResult:
    deleted_count: int
    skipped_count: int
    freed_bytes: int
    chunk_ids: list[str]


class StorageManager:
    def __init__(
        self,
        config: AppConfig,
        repo: StateRepository,
        paths: StoragePaths,
        enabled_target_ids: list[str] | None = None,
    ) -> None:
        self._config = config
        self._repo = repo
        self._paths = paths
        self._enabled_targets = enabled_target_ids or []

    def disk_usage(self) -> dict:
        total, used, free = shutil.disk_usage(self._paths.base)
        chunk_stats = self._repo.chunk_storage_stats()
        return {
            "base_path": str(self._paths.base),
            "recordings_path": str(self._paths.recordings),
            "disk_total_bytes": total,
            "disk_used_bytes": used,
            "disk_free_bytes": free,
            "disk_used_percent": round((used / total) * 100, 2) if total else 0.0,
            "max_usage_percent": self._config.storage.max_usage_percent,
            "quota_exceeded": self.is_quota_exceeded(),
            **chunk_stats,
        }

    def is_quota_exceeded(self) -> bool:
        total, used, _ = shutil.disk_usage(self._paths.base)
        if not total:
            return False
        return (used / total) * 100 >= self._config.storage.max_usage_percent

    def list_chunks(self, query: ChunkQuery):
        return self._repo.list_chunks(query)

    def delete_chunks_in_range(
        self,
        start: datetime,
        end: datetime,
        *,
        dry_run: bool = False,
        campaign_id: str | None = None,
        subject_user_id: str | None = None,
    ) -> DeleteResult:
        query = ChunkQuery(
            start_ts=start,
            end_ts=end,
            campaign_id=campaign_id,
            subject_user_id=subject_user_id,
            status="complete",
            limit=10_000,
        )
        chunks = self._repo.list_chunks(query)
        deleted: list[str] = []
        skipped = 0
        freed = 0

        for chunk in chunks:
            if self._config.storage.delete_only_if_replicated and self._enabled_targets:
                if not self._repo.chunk_fully_replicated(chunk.chunk_id, self._enabled_targets):
                    skipped += 1
                    continue

            if not dry_run:
                path = Path(chunk.path)
                if path.exists():
                    freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                    shutil.rmtree(path)
                self._repo.mark_chunk_deleted(chunk.chunk_id)
            else:
                freed += chunk.byte_size
            deleted.append(chunk.chunk_id)

        if not dry_run and deleted:
            logger.info(
                "Deleted %d chunks (%d bytes freed), skipped %d",
                len(deleted),
                freed,
                skipped,
            )

        return DeleteResult(
            deleted_count=len(deleted),
            skipped_count=skipped,
            freed_bytes=freed,
            chunk_ids=deleted,
        )

    def delete_chunk(self, chunk_id: str) -> bool:
        chunk = self._repo.get_chunk(chunk_id)
        if chunk is None or chunk.status == "deleted":
            return False

        if self._config.storage.delete_only_if_replicated and self._enabled_targets:
            if not self._repo.chunk_fully_replicated(chunk_id, self._enabled_targets):
                return False

        path = Path(chunk.path)
        if path.exists():
            shutil.rmtree(path)
        self._repo.mark_chunk_deleted(chunk_id)
        logger.info("Deleted chunk %s", chunk_id)
        return True

    def apply_retention(self) -> DeleteResult:
        if self._config.storage.retention_days <= 0:
            return DeleteResult(0, 0, 0, [])

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._config.storage.retention_days)
        query = ChunkQuery(end_ts=cutoff, status="complete", limit=10_000)
        chunks = self._repo.list_chunks(query)

        if not chunks:
            return DeleteResult(0, 0, 0, [])

        oldest = min(c.start_ts for c in chunks)
        newest = max(c.end_ts for c in chunks)
        return self.delete_chunks_in_range(oldest, newest)
