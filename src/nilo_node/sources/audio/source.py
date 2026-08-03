"""Audio DataSource plugin — per-mic Bluetooth tracks in chunks."""

from __future__ import annotations

from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.monitoring.models import Campaign, RecordingRun
from nilo_node.sources.base import ChunkContext, SourceHealth, SourceManifest


class AudioSource:
    def __init__(self, source_id: str, bluetooth: BluetoothManager) -> None:
        self.source_id = source_id
        self._bluetooth = bluetooth

    async def on_campaign_start(self, campaign: Campaign) -> None:
        self._bluetooth.set_campaign(campaign)

    async def on_campaign_stop(self, campaign: Campaign) -> None:
        return None

    async def on_run_start(self, run: RecordingRun) -> None:
        return None

    async def on_run_stop(self, run: RecordingRun) -> None:
        return None

    async def on_chunk_open(self, ctx: ChunkContext) -> None:
        audio_dir = ctx.chunk_path / "sources" / self.source_id
        audio_dir.mkdir(parents=True, exist_ok=True)
        await self._bluetooth.begin_chunk(ctx.chunk_id, ctx.chunk_path)

    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest:
        manifest = await self._bluetooth.finalize_chunk(ctx.chunk_id)
        if manifest is None:
            return SourceManifest(
                path=f"sources/{self.source_id}/",
                stub=True,
                extra={"skipped": True, "reason": "audio disabled"},
            )
        return manifest

    async def on_chunk_abort(self, ctx: ChunkContext) -> None:
        await self._bluetooth.abort_chunk(ctx.chunk_id)

    def health(self) -> SourceHealth:
        status = self._bluetooth.get_status()
        if not status.enabled:
            return SourceHealth(source_id=self.source_id, status="disabled")
        if status.mock:
            return SourceHealth(source_id=self.source_id, status="mock")
        if status.connected_count > 0:
            return SourceHealth(source_id=self.source_id, status="ok")
        return SourceHealth(source_id=self.source_id, status="idle")
