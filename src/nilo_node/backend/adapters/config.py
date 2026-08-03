"""Campaign/config pull adapter."""

from __future__ import annotations

import logging

from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport
from nilo_node.monitoring.models import Campaign

logger = logging.getLogger(__name__)


class ConfigAdapter:
    adapter_id = "config"
    requires_endpoint = "campaign"

    async def fetch(
        self,
        transport: BackendTransport,
        node_id: str,
        endpoint_path: str,
    ) -> Campaign | None:
        if not endpoint_path:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.campaign is not configured"
            )

        logger.debug("Fetching campaign from backend: GET %s", endpoint_path)
        payload = await transport.get_json(endpoint_path)

        if "campaign" in payload:
            payload = payload["campaign"]
        return Campaign.model_validate(payload)
