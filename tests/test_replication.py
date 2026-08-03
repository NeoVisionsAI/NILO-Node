"""Tests for NAS replication target."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from nilo_node.config.models import NasReplicationTarget
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.storage.paths import StoragePaths
from nilo_node.storage.replication.nas_target import NasMirrorTarget


@pytest.mark.asyncio
async def test_nas_mirror_copy(tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    chunk_path = (
        recordings / "campaigns" / "c1" / "runs" / "r1" / "chunks" / "chunk1"
    )
    chunk_path.mkdir(parents=True)
    (chunk_path / "manifest.json").write_text('{"ok": true}', encoding="utf-8")

    nas_mount = tmp_path / "nas"
    nas_mount.mkdir()

    paths = StoragePaths(tmp_path, "recordings")
    target = NasMirrorTarget(
        NasReplicationTarget(
            enabled=True,
            mount_path=str(nas_mount),
            relative_path="nilo-node",
            method="copy",
        ),
        paths,
    )

    chunk = ChunkRecord(
        chunk_id="chunk1",
        campaign_id="c1",
        campaign_name="test",
        recording_run_id="r1",
        subject_user_id=None,
        node_id="n1",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        path=str(chunk_path),
        status="complete",
    )

    await target.replicate(chunk, chunk_path)

    dest = (
        nas_mount
        / "nilo-node"
        / "campaigns"
        / "c1"
        / "runs"
        / "r1"
        / "chunks"
        / "chunk1"
        / "manifest.json"
    )
    assert dest.exists()
