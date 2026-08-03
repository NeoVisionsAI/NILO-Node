"""Chunk manifest builder."""

from __future__ import annotations

from datetime import datetime
from typing import Any

MANIFEST_SCHEMA_VERSION = "1.0"


def build_manifest(
    *,
    chunk_id: str,
    campaign_id: str,
    campaign_name: str,
    recording_run_id: str,
    subject_user_id: str | None,
    node_id: str,
    start: datetime,
    end: datetime,
    chunk_duration_sec: int,
    sources: dict[str, Any],
) -> dict[str, Any]:
    """Build chunk manifest.json. subject_user_id is always present (nullable)."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "recording_run_id": recording_run_id,
        "node_id": node_id,
        "subject_user_id": subject_user_id,
        "time_range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "chunk_duration_sec": chunk_duration_sec,
        "sources_present": sorted(sources.keys()),
        "sources": sources,
    }
