"""Cardmed-Dev registration and photo ingest orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nilo_node.backend.client import BackendClient
from nilo_node.cardmed.models import (
    CardmedAssignment,
    CardmedRegisterRequest,
    CardmedStatusResponse,
    CardmedDeviceStatus,
    PhotoIngestResult,
)
from nilo_node.config.models import AppConfig
from nilo_node.sources.physiology.store import PhysiologyStore
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)

_ONLINE_WINDOW_SEC = 120


class CardmedService:
    def __init__(
        self,
        config: AppConfig,
        repo: StateRepository,
        node_id: str,
        physiology_store: PhysiologyStore,
        backend: BackendClient,
    ) -> None:
        self._config = config
        self._repo = repo
        self._node_id = node_id
        self._store = physiology_store
        self._backend = backend

    def register(self, request: CardmedRegisterRequest) -> CardmedAssignment:
        now = datetime.now(timezone.utc)
        assignment = CardmedAssignment(
            device_id=request.device_id,
            node_id=self._node_id,
            device_name=request.device_name,
            mac_address=request.mac_address,
            registered_at=now,
            last_seen_at=now,
            metadata=request.metadata,
        )
        self._repo.upsert_cardmed_assignment(assignment)
        self._repo.upsert_device(
            device_id=request.device_id,
            device_type="cardmed",
            status="registered",
            metadata={
                "device_name": request.device_name,
                "mac_address": request.mac_address,
                **request.metadata,
            },
        )
        self._repo.insert_device_event(
            request.device_id,
            "registered",
            {"node_id": self._node_id},
        )
        logger.info("Cardmed registered: device_id=%s", request.device_id)
        return assignment

    def unregister(self, device_id: str) -> bool:
        removed = self._repo.delete_cardmed_assignment(device_id)
        if removed:
            self._repo.upsert_device(device_id, "cardmed", "unregistered", {})
            self._repo.insert_device_event(device_id, "unregistered", {})
            logger.info("Cardmed unregistered: device_id=%s", device_id)
        return removed

    def touch_device(self, device_id: str) -> None:
        self._repo.touch_cardmed_assignment(device_id)
        self._repo.upsert_device(device_id, "cardmed", "online", {})

    async def ingest_photo(
        self,
        *,
        device_id: str,
        data: bytes,
        mime_type: str,
        capture_ts: datetime,
        reading_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PhotoIngestResult:
        assignment = self._repo.get_cardmed_assignment(device_id)
        if assignment is None:
            raise LookupError(f"Device not registered: {device_id}")

        self.touch_device(device_id)
        result = self._store.ingest_photo(
            device_id=device_id,
            data=data,
            mime_type=mime_type,
            capture_ts=capture_ts,
            reading_id=reading_id,
            metadata=metadata,
        )

        if self._config.cardmed.forward_to_backend:
            forwarded = await self._backend.forward_physiology(
                {
                    "node_id": self._node_id,
                    "device_id": device_id,
                    "reading_id": result.reading_id,
                    "chunk_id": result.chunk_id,
                    "capture_ts": capture_ts.isoformat(),
                    "image_path": result.image_path,
                    "late": result.late,
                    "metadata": metadata or {},
                }
            )
            result = result.model_copy(update={"forwarded": forwarded})

        return result

    def get_status(self) -> CardmedStatusResponse:
        now = datetime.now(timezone.utc)
        online_cutoff = now - timedelta(seconds=_ONLINE_WINDOW_SEC)
        devices: list[CardmedDeviceStatus] = []
        for row in self._repo.list_cardmed_assignments():
            last_seen = row.last_seen_at
            devices.append(
                CardmedDeviceStatus(
                    device_id=row.device_id,
                    device_name=row.device_name,
                    mac_address=row.mac_address,
                    registered_at=row.registered_at,
                    last_seen_at=last_seen,
                    online=last_seen >= online_cutoff,
                )
            )
        return CardmedStatusResponse(
            node_id=self._node_id,
            registered_count=len(devices),
            devices=devices,
        )
