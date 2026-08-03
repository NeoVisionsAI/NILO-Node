"""Physiology photo ingest into active or historical chunks."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nilo_node.cardmed.models import PhotoIngestResult
from nilo_node.config.models import CardmedConfig
from nilo_node.monitoring.models import ChunkRecord
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)

_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class PhysiologyStore:
    """Writes Cardmed photos and index entries into chunk physiology folders."""

    def __init__(
        self,
        config: CardmedConfig,
        repo: StateRepository,
    ) -> None:
        self._config = config
        self._repo = repo

    def ingest_photo(
        self,
        *,
        device_id: str,
        data: bytes,
        mime_type: str,
        capture_ts: datetime,
        reading_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PhotoIngestResult:
        if mime_type not in self._config.allowed_mime_types:
            raise ValueError(f"Unsupported mime type: {mime_type}")

        max_bytes = self._config.max_upload_size_mb * 1024 * 1024
        if len(data) > max_bytes:
            raise ValueError(f"Upload exceeds {self._config.max_upload_size_mb} MB limit")

        reading_id = reading_id or str(uuid.uuid4())
        capture_ts = capture_ts.astimezone(timezone.utc)
        chunk, late = self._resolve_chunk(capture_ts)

        if chunk is None:
            raise RuntimeError("No chunk available for physiology ingest")

        chunk_path = Path(chunk.path)
        physiology_dir = chunk_path / "sources" / "physiology"
        if late:
            physiology_dir = physiology_dir / "late"
        images_dir = physiology_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        ext = _MIME_EXT.get(mime_type, ".bin")
        ts_label = capture_ts.strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
        image_name = f"{ts_label}_{reading_id}{ext}"
        image_path = images_dir / image_name
        image_path.write_bytes(data)

        rel_image = str(image_path.relative_to(chunk_path))
        index_path = chunk_path / "sources" / "physiology" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)

        entry: dict[str, Any] = {
            "capture_ts": capture_ts.isoformat(),
            "reading_id": reading_id,
            "image": rel_image.replace("\\", "/"),
            "device_id": device_id,
        }
        if metadata:
            entry["metadata"] = metadata
        if late:
            entry["late"] = True
            entry["target_chunk_id"] = chunk.chunk_id

        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")

        logger.info(
            "Physiology photo ingested: reading_id=%s chunk=%s late=%s",
            reading_id,
            chunk.chunk_id,
            late,
        )
        return PhotoIngestResult(
            reading_id=reading_id,
            chunk_id=chunk.chunk_id,
            image_path=rel_image.replace("\\", "/"),
            index_path=str(index_path.relative_to(chunk_path)).replace("\\", "/"),
            late=late,
        )

    def _resolve_chunk(self, capture_ts: datetime) -> tuple[ChunkRecord | None, bool]:
        open_chunk = self._repo.get_open_chunk()
        if open_chunk is not None and self._ts_in_chunk(capture_ts, open_chunk):
            return open_chunk, False

        matched = self._repo.find_chunk_for_timestamp(capture_ts)
        if matched is not None:
            if matched.status == "open":
                return matched, False
            return matched, True

        if open_chunk is not None:
            late = not self._ts_in_chunk(capture_ts, open_chunk)
            return open_chunk, late

        latest = self._repo.get_latest_chunk()
        if latest is not None:
            return latest, True

        return None, False

    def has_open_chunk(self) -> bool:
        return self._repo.get_open_chunk() is not None

    @staticmethod
    def _ts_in_chunk(ts: datetime, chunk: ChunkRecord) -> bool:
        start = chunk.start_ts
        end = chunk.end_ts
        return start <= ts < end
