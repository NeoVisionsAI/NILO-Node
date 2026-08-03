"""Filesystem path helpers for recordings layout."""

from __future__ import annotations

from pathlib import Path


class StoragePaths:
    """Canonical paths under storage.base_path."""

    def __init__(self, base_path: Path, recordings_dir: str = "recordings") -> None:
        self.base = base_path
        self.recordings = base_path / recordings_dir

    def campaigns_root(self) -> Path:
        return self.recordings / "campaigns"

    def campaign_dir(self, campaign_id: str) -> Path:
        return self.campaigns_root() / campaign_id

    def run_dir(self, campaign_id: str, recording_run_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "runs" / recording_run_id

    def chunk_dir(self, campaign_id: str, recording_run_id: str, chunk_id: str) -> Path:
        return self.run_dir(campaign_id, recording_run_id) / "chunks" / chunk_id

    def relative_from_recordings(self, absolute: Path) -> Path:
        return absolute.relative_to(self.recordings)
