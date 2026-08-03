"""DataSource plugin protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from nilo_node.monitoring.models import Campaign, RecordingRun


@dataclass(frozen=True)
class ChunkContext:
    chunk_id: str
    chunk_path: Path
    campaign_id: str
    campaign_name: str
    recording_run_id: str
    subject_user_id: str | None
    node_id: str
    start_ts: datetime
    chunk_duration_sec: int


class SourceManifest(BaseModel):
    path: str
    stub: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class SourceHealth(BaseModel):
    source_id: str
    status: str = "ok"


class DataSource(Protocol):
    source_id: str

    async def on_campaign_start(self, campaign: Campaign) -> None: ...
    async def on_campaign_stop(self, campaign: Campaign) -> None: ...
    async def on_run_start(self, run: RecordingRun) -> None: ...
    async def on_run_stop(self, run: RecordingRun) -> None: ...
    async def on_chunk_open(self, ctx: ChunkContext) -> None: ...
    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest: ...
    async def on_chunk_abort(self, ctx: ChunkContext) -> None: ...
    def health(self) -> SourceHealth: ...
