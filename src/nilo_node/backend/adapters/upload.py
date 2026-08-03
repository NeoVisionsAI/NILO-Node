"""Chunk file upload adapter (tar.gz archive until contract is finalized)."""

from __future__ import annotations

import io
import logging
import tarfile
from pathlib import Path

from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport

logger = logging.getLogger(__name__)


def build_chunk_archive(chunk_path: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for file_path in sorted(chunk_path.rglob("*")):
            if file_path.is_file():
                archive.add(file_path, arcname=str(file_path.relative_to(chunk_path)))
    return buffer.getvalue()


class UploadAdapter:
    adapter_id = "upload"
    requires_endpoint = "upload"

    async def send(
        self,
        transport: BackendTransport,
        node_id: str,
        endpoint_path: str,
        *,
        chunk_id: str,
        chunk_path: Path,
    ) -> None:
        if not endpoint_path:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.upload is not configured"
            )

        archive = build_chunk_archive(chunk_path)
        logger.debug(
            "Uploading chunk archive to backend: POST %s chunk=%s bytes=%d",
            endpoint_path,
            chunk_id,
            len(archive),
        )
        response = await transport.request(
            "POST",
            endpoint_path,
            data={"node_id": node_id, "chunk_id": chunk_id},
            files={
                "archive": (f"{chunk_id}.tar.gz", archive, "application/gzip"),
            },
        )
        if response.status_code >= 400:
            from nilo_node.backend.exceptions import BackendRequestError

            raise BackendRequestError(
                f"POST {endpoint_path} failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
