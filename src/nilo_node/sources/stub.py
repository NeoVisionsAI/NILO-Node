"""Stub data source for Phase 0."""

from __future__ import annotations

from pathlib import Path

from nilo_node.monitoring.models import Campaign, RecordingRun
from nilo_node.sources.base import ChunkContext, SourceHealth, SourceManifest


class StubSource:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    async def on_campaign_start(self, campaign: Campaign) -> None:
        return None

    async def on_campaign_stop(self, campaign: Campaign) -> None:
        return None

    async def on_run_start(self, run: RecordingRun) -> None:
        return None

    async def on_run_stop(self, run: RecordingRun) -> None:
        return None

    async def on_chunk_open(self, ctx: ChunkContext) -> None:
        source_dir = ctx.chunk_path / "sources" / self.source_id
        source_dir.mkdir(parents=True, exist_ok=True)

    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest:
        source_dir = ctx.chunk_path / "sources" / self.source_id
        marker = source_dir / "stub.dat"
        marker.write_text(f"stub:{self.source_id}:{ctx.chunk_id}\n", encoding="utf-8")
        return SourceManifest(path=str(Path("sources") / self.source_id / "stub.dat"), stub=True)

    async def on_chunk_abort(self, ctx: ChunkContext) -> None:
        return None

    def health(self) -> SourceHealth:
        return SourceHealth(source_id=self.source_id, status="ok")
