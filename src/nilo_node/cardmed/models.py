"""Cardmed-Dev API and persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CardmedRegisterRequest(BaseModel):
    device_id: str
    device_name: str | None = None
    mac_address: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CardmedAssignment(BaseModel):
    device_id: str
    node_id: str
    device_name: str | None = None
    mac_address: str | None = None
    registered_at: datetime
    last_seen_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class CardmedDeviceStatus(BaseModel):
    device_id: str
    device_name: str | None = None
    mac_address: str | None = None
    registered_at: datetime
    last_seen_at: datetime
    online: bool = False


class CardmedStatusResponse(BaseModel):
    node_id: str
    registered_count: int
    devices: list[CardmedDeviceStatus] = Field(default_factory=list)


class PhotoIngestResult(BaseModel):
    reading_id: str
    chunk_id: str | None
    image_path: str
    index_path: str
    late: bool = False
    forwarded: bool = False
