"""Mirror chunks to a NAS mount path."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from nilo_node.config.models import NasReplicationTarget
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.storage.paths import StoragePaths

logger = logging.getLogger(__name__)


class NasMirrorTarget:
    target_id = "nas"

    def __init__(self, target_cfg: NasReplicationTarget, paths: StoragePaths) -> None:
        self._cfg = target_cfg
        self._paths = paths

    def _destination_root(self) -> Path:
        return Path(self._cfg.mount_path) / self._cfg.relative_path

    def _destination_for_chunk(self, chunk_path: Path) -> Path:
        relative = self._paths.relative_from_recordings(chunk_path)
        return self._destination_root() / relative

    async def replicate(self, chunk: ChunkRecord, chunk_path: Path) -> None:
        mount = Path(self._cfg.mount_path)
        if not mount.exists():
            raise FileNotFoundError(f"NAS mount not available: {mount}")

        dest = self._destination_for_chunk(chunk_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if self._cfg.method == "rsync":
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--delete",
                    f"{chunk_path}/",
                    f"{dest}/",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(chunk_path, dest)

        logger.info("NAS replication complete: %s → %s", chunk.chunk_id, dest)
