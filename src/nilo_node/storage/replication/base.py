"""Replication target protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from nilo_node.monitoring.models import ChunkRecord


class ReplicationTarget(Protocol):
    target_id: str

    async def replicate(self, chunk: ChunkRecord, chunk_path: Path) -> None: ...
