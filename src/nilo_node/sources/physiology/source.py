"""Physiology DataSource plugin — chunk lifecycle + Cardmed ingest."""

from __future__ import annotations

from pathlib import Path

from nilo_node.monitoring.models import Campaign, RecordingRun
from nilo_node.sources.base import ChunkContext, SourceHealth, SourceManifest
from nilo_node.sources.physiology.store import PhysiologyStore


class PhysiologySource:
    def __init__(self, source_id: str, store: PhysiologyStore) -> None:
        self.source_id = source_id
        self._store = store

    @property
    def store(self) -> PhysiologyStore:
        return self._store

    async def on_campaign_start(self, campaign: Campaign) -> None:
        return None

    async def on_campaign_stop(self, campaign: Campaign) -> None:
        return None

    async def on_run_start(self, run: RecordingRun) -> None:
        return None

    async def on_run_stop(self, run: RecordingRun) -> None:
        return None

    async def on_chunk_open(self, ctx: ChunkContext) -> None:
        physiology_dir = ctx.chunk_path / "sources" / self.source_id
        (physiology_dir / "images").mkdir(parents=True, exist_ok=True)

    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest:
        index_path = ctx.chunk_path / "sources" / self.source_id / "index.jsonl"
        rel_index = Path("sources") / self.source_id / "index.jsonl"
        extra: dict[str, object] = {"entries": 0}
        if index_path.exists():
            lines = [
                line for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            extra["entries"] = len(lines)
        else:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.touch()

        return SourceManifest(path=str(rel_index), extra=extra)

    async def on_chunk_abort(self, ctx: ChunkContext) -> None:
        return None

    def health(self) -> SourceHealth:
        status = "ok" if self._store.has_open_chunk() else "idle"
        return SourceHealth(source_id=self.source_id, status=status)
