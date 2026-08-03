"""Chunk query and deletion endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.config.models import AppConfig
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.models import ChunkQuery
from nilo_node.storage.replication.manager import ReplicationManager


class DeleteChunksRequest(BaseModel):
    start: datetime
    end: datetime
    campaign_id: str | None = None
    subject_user_id: str | None = None
    dry_run: bool = False


class ReplicatePendingRequest(BaseModel):
    pass


def create_chunks_router(
    config: AppConfig,
    storage_manager: StorageManager,
    replication_manager: ReplicationManager,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/chunks", tags=["chunks"])
    auth = Depends(require_auth(config))

    @router.get("", dependencies=[auth])
    async def list_chunks(
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
        campaign_id: str | None = Query(None),
        campaign_name: str | None = Query(None),
        subject_user_id: str | None = Query(None),
        status: str | None = Query("complete"),
        limit: int = Query(500, ge=1, le=10_000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        query = ChunkQuery(
            start_ts=start,
            end_ts=end,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            subject_user_id=subject_user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        chunks = storage_manager.list_chunks(query)
        return {
            "count": len(chunks),
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "campaign_id": c.campaign_id,
                    "campaign_name": c.campaign_name,
                    "recording_run_id": c.recording_run_id,
                    "subject_user_id": c.subject_user_id,
                    "start_ts": c.start_ts.isoformat(),
                    "end_ts": c.end_ts.isoformat(),
                    "path": c.path,
                    "status": c.status,
                    "byte_size": c.byte_size,
                    "sources_present": c.sources_present,
                }
                for c in chunks
            ],
        }

    @router.post("/delete", dependencies=[auth])
    async def delete_chunks(body: DeleteChunksRequest) -> dict[str, Any]:
        if body.end <= body.start:
            raise HTTPException(status_code=400, detail="end must be after start")
        result = storage_manager.delete_chunks_in_range(
            body.start,
            body.end,
            dry_run=body.dry_run,
            campaign_id=body.campaign_id,
            subject_user_id=body.subject_user_id,
        )
        return {
            "dry_run": body.dry_run,
            "deleted_count": result.deleted_count,
            "skipped_count": result.skipped_count,
            "freed_bytes": result.freed_bytes,
            "chunk_ids": result.chunk_ids,
        }

    @router.post("/replicate", dependencies=[auth])
    async def replicate_pending() -> dict[str, int]:
        if not config.replication.enabled:
            raise HTTPException(status_code=400, detail="Replication is disabled")
        processed = await replication_manager.process_pending()
        return {"processed": processed}

    return router
