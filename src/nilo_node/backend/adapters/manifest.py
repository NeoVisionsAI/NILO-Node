"""Chunk manifest notification adapter."""

from __future__ import annotations

import logging
from typing import Any

from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport

logger = logging.getLogger(__name__)


class ManifestAdapter:
    adapter_id = "manifest"
    requires_endpoint = "manifest"

    async def send(
        self,
        transport: BackendTransport,
        node_id: str,
        endpoint_path: str,
        *,
        chunk_id: str,
        manifest: dict[str, Any],
    ) -> None:
        if not endpoint_path:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.manifest is not configured"
            )

        payload = {
            "node_id": node_id,
            "chunk_id": chunk_id,
            "manifest": manifest,
        }
        logger.debug("Sending chunk manifest to backend: POST %s chunk=%s", endpoint_path, chunk_id)
        await transport.post_json(endpoint_path, payload)
