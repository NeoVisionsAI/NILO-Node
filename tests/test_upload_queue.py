"""Tests for offline upload queue."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from nilo_node.backend.client import BackendClient
from nilo_node.backend.transport import BackendTransport
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository


def _chunk_record(chunk_path: Path) -> ChunkRecord:
    now = datetime.now(timezone.utc)
    return ChunkRecord(
        chunk_id="chunk-upload-1",
        campaign_id="camp-1",
        campaign_name="test",
        recording_run_id="run-1",
        subject_user_id=None,
        node_id="node-1",
        start_ts=now,
        end_ts=now,
        path=str(chunk_path),
        status="complete",
    )


@pytest.mark.asyncio
async def test_upload_queue_processes_manifest_and_upload(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/nodes/node-1/manifests":
            return httpx.Response(204)
        if request.url.path == "/nodes/node-1/upload":
            return httpx.Response(204)
        return httpx.Response(404)

    chunk_path = tmp_path / "chunk1"
    chunk_path.mkdir()
    (chunk_path / "manifest.json").write_text('{"chunk_id":"chunk-upload-1"}', encoding="utf-8")
    (chunk_path / "data.bin").write_bytes(b"payload")

    config = AppConfig.model_validate(
        {
            "backend": {
                "enabled": True,
                "base_url": "https://api.test",
                "auth": {"mode": "none"},
                "endpoints": {
                    "manifest": "/nodes/{node_id}/manifests",
                    "upload": "/nodes/{node_id}/upload",
                },
                "adapters": {
                    "manifest": {"enabled": True},
                    "upload": {"enabled": True},
                },
                "upload_queue": {"enabled": True, "process_interval_sec": 999},
            },
            "storage": {"base_path": str(tmp_path)},
        }
    )

    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    chunk = _chunk_record(chunk_path)
    repo.insert_chunk(chunk)

    repo.insert_upload_job(chunk.chunk_id, "manifest")
    repo.insert_upload_job(chunk.chunk_id, "upload")

    transport = httpx.MockTransport(handler)
    client = BackendClient(config, tmp_path, "node-1")

    class PatchedTransport(BackendTransport):
        async def start(self) -> None:
            self._client = httpx.AsyncClient(
                transport=transport,
                base_url=config.backend.base_url,
                timeout=httpx.Timeout(config.backend.request_timeout_sec),
            )

    client._transport = PatchedTransport(config, client._auth, "node-1")
    client._started = False
    await client.start()

    queue = UploadQueueService(config, repo)
    processed = await queue.process_pending(client)

    assert processed == 2
    assert ("POST", "/nodes/node-1/manifests") in calls
    assert ("POST", "/nodes/node-1/upload") in calls
    stats = repo.upload_queue_stats()
    assert stats["complete"] == 2
    await client.close()
    db.close()


@pytest.mark.asyncio
async def test_upload_queue_enqueues_on_backend_target_failure(tmp_path: Path) -> None:
    chunk_path = tmp_path / "chunk2"
    chunk_path.mkdir()
    (chunk_path / "manifest.json").write_text("{}", encoding="utf-8")

    config = AppConfig.model_validate(
        {
            "backend": {
                "enabled": True,
                "base_url": "https://api.test",
                "auth": {"mode": "none"},
                "endpoints": {
                    "manifest": "/nodes/{node_id}/manifests",
                },
                "adapters": {"manifest": {"enabled": True}},
                "upload_queue": {"enabled": True},
            },
            "replication": {
                "enabled": True,
                "targets": {"backend": {"enabled": True}},
            },
            "storage": {"base_path": str(tmp_path)},
        }
    )

    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    chunk = _chunk_record(chunk_path)
    repo.insert_chunk(chunk)

    backend = BackendClient(config, tmp_path, "node-1")
    queue = UploadQueueService(config, repo)

    from nilo_node.storage.replication.backend_target import BackendUploadTarget

    target = BackendUploadTarget(config, backend, queue)

    with pytest.raises(Exception):
        await target.replicate(chunk, chunk_path)

    stats = queue.stats()
    assert stats["pending"] >= 1
    db.close()
