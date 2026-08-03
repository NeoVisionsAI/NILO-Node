"""Chunk list filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChunkQuery:
    start_ts: datetime | None = None
    end_ts: datetime | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    subject_user_id: str | None = None
    status: str | None = "complete"
    limit: int = 500
    offset: int = 0
