"""Periodic health and telemetry reporter."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.controller import CampaignController
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.storage.manager import StorageManager
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)


class HealthReporter:
    def __init__(
        self,
        config: AppConfig,
        repo: StateRepository,
        backend: BackendClient,
        campaign_controller: CampaignController,
        storage_manager: StorageManager,
        camera_manager: CameraManager,
        cardmed_service: CardmedService,
        wifi_manager: WifiApManager,
        bluetooth_manager: BluetoothManager,
        upload_queue: UploadQueueService,
        node_id: str,
        version: str,
    ) -> None:
        self._config = config
        self._repo = repo
        self._backend = backend
        self._campaign = campaign_controller
        self._storage = storage_manager
        self._camera = camera_manager
        self._cardmed = cardmed_service
        self._wifi = wifi_manager
        self._bluetooth = bluetooth_manager
        self._upload_queue = upload_queue
        self._node_id = node_id
        self._version = version
        self._started_at = datetime.now(timezone.utc)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        interval = self._config.backend.heartbeat_interval_sec
        while True:
            payload = self.build_payload()
            self._repo.save_heartbeat(payload)
            await self._backend.send_heartbeat(payload)
            await asyncio.sleep(interval)

    def build_payload(self) -> dict[str, Any]:
        usage = self._storage.disk_usage()
        campaign = self._campaign.campaign
        run = self._campaign.recording_run

        return {
            "node_id": self._node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_sec": int(
                (datetime.now(timezone.utc) - self._started_at).total_seconds()
            ),
            "version": self._version,
            "storage": {
                "disk_total_bytes": usage["disk_total_bytes"],
                "disk_used_bytes": usage["disk_used_bytes"],
                "disk_used_percent": usage["disk_used_percent"],
                "quota_exceeded": usage["quota_exceeded"],
                "complete_chunks": usage["complete_chunks"],
                "complete_bytes": usage["complete_bytes"],
                "by_campaign": usage["by_campaign"],
            },
            "monitoring": {
                "capture_active": self._campaign.capture_active,
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
                "recording_run": (
                    {
                        "recording_run_id": run.recording_run_id,
                        "subject_user_id": run.subject_user_id,
                        "start_ts": run.start_ts.isoformat(),
                    }
                    if run
                    else None
                ),
            },
            "services": {
                "orchestrator": "running",
                "api": "running",
                "wifi_ap": (
                    "mock" if self._wifi.get_status().mock else "running"
                    if self._wifi.get_status().running
                    else "stopped"
                ),
                "bluetooth": (
                    "mock" if self._bluetooth.get_status().mock else "running"
                    if self._bluetooth.get_status().adapter_state.value in ("running", "mock")
                    else "stopped"
                ),
            },
            "backend": self._backend.connectivity.to_dict(),
            "upload_queue": self._upload_queue.stats(),
            "camera": self._camera.get_status().model_dump(),
            "cardmed": self._cardmed.get_status().model_dump(),
            "wifi": self._wifi.get_status().model_dump(),
            "bluetooth": self._bluetooth.get_status().model_dump(),
        }
