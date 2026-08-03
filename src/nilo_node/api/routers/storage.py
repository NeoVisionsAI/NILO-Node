"""Storage usage and quota endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from nilo_node.api.deps import require_auth
from nilo_node.config.models import AppConfig
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.replication.manager import ReplicationManager


def create_storage_router(
    config: AppConfig,
    storage_manager: StorageManager,
    replication_manager: ReplicationManager,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
    auth = Depends(require_auth(config))

    @router.get("/usage", dependencies=[auth])
    async def storage_usage() -> dict:
        usage = storage_manager.disk_usage()
        usage["replication"] = {
            "enabled": config.replication.enabled,
            "mode": config.replication.mode,
            "targets": replication_manager.enabled_target_ids,
        }
        return usage

    return router
