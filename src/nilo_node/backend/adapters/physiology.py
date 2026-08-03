"""Forward Cardmed physiology readings to NILO-backend (stub until contract is ready)."""

from __future__ import annotations

import logging
from typing import Any

from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport

logger = logging.getLogger(__name__)


class PhysiologyAdapter:
    adapter_id = "physiology"

    @property
    def requires_endpoint(self) -> str:
        return "physiology"

    async def forward(
        self,
        transport: BackendTransport,
        node_id: str,
        endpoint_path: str,
        payload: dict[str, Any],
    ) -> bool:
        if not endpoint_path:
            raise BackendEndpointNotConfiguredError("physiology endpoint not configured")

        try:
            await transport.post_json(endpoint_path, payload)
            logger.debug(
                "Physiology forwarded: reading_id=%s chunk_id=%s",
                payload.get("reading_id"),
                payload.get("chunk_id"),
            )
            return True
        except Exception as exc:
            logger.warning("Physiology forward failed (stub): %s", exc)
            return False
