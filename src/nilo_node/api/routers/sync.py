"""Sync status endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from nilo_node.api.deps import require_auth
from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.config.models import AppConfig


def create_sync_router(
    config: AppConfig,
    backend: BackendClient,
    upload_queue: UploadQueueService,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/sync", tags=["sync"])
    auth = Depends(require_auth(config))

    @router.get("/status", dependencies=[auth])
    async def sync_status() -> dict[str, Any]:
        connectivity = backend.connectivity.to_dict()
        connectivity["within_offline_grace"] = backend.connectivity.is_within_grace(
            config.monitoring.offline_grace_sec
        )
        return {
            "backend": connectivity,
            "upload_queue": {
                "enabled": config.backend.upload_queue.enabled,
                "stats": upload_queue.stats(),
            },
            "adapters": {
                "manifest": {
                    "enabled": config.adapter_enabled("manifest"),
                    "endpoint_configured": bool(config.backend.endpoints.manifest),
                },
                "upload": {
                    "enabled": config.adapter_enabled("upload"),
                    "endpoint_configured": bool(config.backend.endpoints.upload),
                },
            },
        }

    return router
