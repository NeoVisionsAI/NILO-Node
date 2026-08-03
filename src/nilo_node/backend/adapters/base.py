"""Base adapter for NILO-backend operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from nilo_node.backend.transport import BackendTransport


@runtime_checkable
class BackendAdapter(Protocol):
    adapter_id: str

    @property
    def requires_endpoint(self) -> str:
        """Endpoint field name in BackendEndpoints (e.g. 'campaign')."""
        ...

    async def run(self, transport: BackendTransport, node_id: str) -> object | None: ...
