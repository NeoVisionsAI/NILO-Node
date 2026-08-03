"""Base class for OAK camera stream sources (rgb, tof, pose)."""

from __future__ import annotations

from nilo_node.camera.manager import CameraManager
from nilo_node.monitoring.models import Campaign, RecordingRun
from nilo_node.sources.base import ChunkContext, SourceHealth, SourceManifest


class CameraStreamSource:
    def __init__(self, source_id: str, camera: CameraManager) -> None:
        self.source_id = source_id
        self._camera = camera

    async def on_campaign_start(self, campaign: Campaign) -> None:
        self._camera.set_campaign(campaign)

    async def on_campaign_stop(self, campaign: Campaign) -> None:
        return None

    async def on_run_start(self, run: RecordingRun) -> None:
        return None

    async def on_run_stop(self, run: RecordingRun) -> None:
        return None

    async def on_chunk_open(self, ctx: ChunkContext) -> None:
        await self._camera.begin_chunk(ctx.chunk_id, ctx.chunk_path)

    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest:
        manifest = await self._camera.finalize_source(ctx.chunk_id, self.source_id)
        if manifest is None:
            return SourceManifest(
                path=f"sources/{self.source_id}/",
                stub=True,
                extra={"skipped": True, "reason": "disabled or not connected"},
            )
        return manifest

    async def on_chunk_abort(self, ctx: ChunkContext) -> None:
        await self._camera.abort_chunk(ctx.chunk_id)

    def health(self) -> SourceHealth:
        status = self._camera.get_status()
        if status.connection_state.value == "connected":
            return SourceHealth(source_id=self.source_id, status="ok")
        if status.pipeline_mode == "mock":
            return SourceHealth(source_id=self.source_id, status="mock")
        return SourceHealth(source_id=self.source_id, status="disconnected")
