"""Heartbeat telemetry adapter."""

from __future__ import annotations

import logging
from typing import Any

from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport

logger = logging.getLogger(__name__)


class HeartbeatAdapter:
    adapter_id = "heartbeat"
    requires_endpoint = "heartbeat"

    async def send(
        self,
        transport: BackendTransport,
        node_id: str,
        endpoint_path: str,
        payload: dict[str, Any],
    ) -> None:
        if not endpoint_path:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.heartbeat is not configured"
            )

        logger.debug("Sending heartbeat to backend: POST %s", endpoint_path)
        await transport.post_json(endpoint_path, payload)
