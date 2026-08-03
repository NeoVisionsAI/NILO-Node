"""Tests for storage manager and chunk deletion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.models import ChunkQuery
from nilo_node.storage.paths import StoragePaths


def _insert_chunk(
    repo: StateRepository,
    tmp_path: Path,
    chunk_id: str,
    start: datetime,
    end: datetime,
) -> Path:
    chunk_path = (
        tmp_path
        / "recordings"
        / "campaigns"
        / "c1"
        / "runs"
        / "r1"
        / "chunks"
        / chunk_id
    )
    chunk_path.mkdir(parents=True)
    (chunk_path / "manifest.json").write_text("{}", encoding="utf-8")
    (chunk_path / "data.bin").write_bytes(b"x" * 100)

    repo.insert_chunk(
        ChunkRecord(
            chunk_id=chunk_id,
            campaign_id="c1",
            campaign_name="test",
            recording_run_id="r1",
            subject_user_id="patient-1",
            node_id="node-1",
            start_ts=start,
            end_ts=end,
            path=str(chunk_path),
            status="complete",
            byte_size=100,
        )
    )
    return chunk_path


def test_list_and_delete_chunks_by_range(tmp_path: Path) -> None:
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    paths = StoragePaths(tmp_path, "recordings")
    config = AppConfig.model_validate({"storage": {"base_path": str(tmp_path)}})
    manager = StorageManager(config, repo, paths)

    base = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    _insert_chunk(repo, tmp_path, "chunk-a", base, base + timedelta(minutes=5))
    _insert_chunk(
        repo,
        tmp_path,
        "chunk-b",
        base + timedelta(hours=2),
        base + timedelta(hours=2, minutes=5),
    )

    overlap = manager.list_chunks(
        ChunkQuery(
            start_ts=base + timedelta(minutes=1),
            end_ts=base + timedelta(minutes=6),
        )
    )
    assert len(overlap) == 1
    assert overlap[0].chunk_id == "chunk-a"

    result = manager.delete_chunks_in_range(
        base,
        base + timedelta(minutes=6),
        dry_run=False,
    )
    assert result.deleted_count == 1
    assert result.chunk_ids == ["chunk-a"]
    assert not (paths.chunk_dir("c1", "r1", "chunk-a")).exists()

    remaining = manager.list_chunks(ChunkQuery(status="complete"))
    assert len(remaining) == 1
    assert remaining[0].chunk_id == "chunk-b"

    db.close()


def test_delete_only_if_replicated(tmp_path: Path) -> None:
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)
    paths = StoragePaths(tmp_path, "recordings")
    config = AppConfig.model_validate(
        {
            "storage": {
                "base_path": str(tmp_path),
                "delete_only_if_replicated": True,
            }
        }
    )
    manager = StorageManager(config, repo, paths, enabled_target_ids=["nas"])

    base = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    _insert_chunk(repo, tmp_path, "chunk-x", base, base + timedelta(minutes=5))

    result = manager.delete_chunks_in_range(base, base + timedelta(minutes=6))
    assert result.deleted_count == 0
    assert result.skipped_count == 1

    repo.upsert_chunk_replication("chunk-x", "nas", "complete", base.isoformat())
    result2 = manager.delete_chunks_in_range(base, base + timedelta(minutes=6))
    assert result2.deleted_count == 1

    db.close()
