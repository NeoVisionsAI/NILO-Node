"""FastAPI application and modular routers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from nilo_node import __version__
from nilo_node.api.routers.bluetooth import create_bluetooth_router
from nilo_node.api.routers.camera import create_camera_router
from nilo_node.api.routers.cardmed import create_cardmed_router
from nilo_node.api.routers.chunks import create_chunks_router
from nilo_node.api.routers.devices import create_devices_router
from nilo_node.api.routers.storage import create_storage_router
from nilo_node.api.routers.sync import create_sync_router
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.controller import CampaignController
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.replication.manager import ReplicationManager


def create_app(
    node_id: str,
    config: AppConfig,
    campaign_controller: CampaignController,
    storage_manager: StorageManager,
    replication_manager: ReplicationManager,
    camera_manager: CameraManager,
    repo: StateRepository,
    cardmed_service: CardmedService,
    wifi_manager: WifiApManager,
    bluetooth_manager: BluetoothManager,
    backend: BackendClient,
    upload_queue: UploadQueueService,
) -> FastAPI:
    app = FastAPI(title="NILO-Node Local API", version=__version__)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/node/info")
    async def node_info() -> dict[str, Any]:
        campaign = campaign_controller.campaign
        wifi_status = wifi_manager.get_status()
        return {
            "node_id": node_id,
            "version": __version__,
            "capture_active": campaign_controller.capture_active,
            "wifi": wifi_status.model_dump(mode="json"),
            "campaign": (
                {
                    "campaign_id": campaign.campaign_id,
                    "campaign_name": campaign.campaign_name,
                    "subject_user_id": campaign.subject_user_id,
                    "status": campaign.status.value,
                }
                if campaign
                else None
            ),
        }

    app.include_router(create_storage_router(config, storage_manager, replication_manager))
    app.include_router(create_sync_router(config, backend, upload_queue))
    app.include_router(create_chunks_router(config, storage_manager, replication_manager))
    app.include_router(create_camera_router(config, camera_manager))
    app.include_router(create_cardmed_router(config, cardmed_service))
    app.include_router(create_bluetooth_router(config, bluetooth_manager))
    app.include_router(
        create_devices_router(
            config,
            repo,
            camera_manager,
            cardmed_service,
            wifi_manager,
            bluetooth_manager,
        )
    )

    return app
