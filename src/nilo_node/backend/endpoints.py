"""Configurable backend endpoint paths (contracts TBD)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from urllib.parse import urljoin


class BackendEndpoints(BaseModel):
    """Relative paths under backend.base_url. Empty string = not configured yet."""

    login: str = ""
    refresh: str = ""
    campaign: str = ""
    heartbeat: str = ""
    manifest: str = ""
    upload: str = ""
    physiology: str = ""

    def resolve(self, path: str, *, node_id: str = "") -> str:
        formatted = path.format(node_id=node_id)
        return formatted

    def url(self, base_url: str, path: str, *, node_id: str = "") -> str:
        relative = self.resolve(path, node_id=node_id)
        return urljoin(base_url.rstrip("/") + "/", relative.lstrip("/"))

    def is_configured(self, name: str) -> bool:
        value = getattr(self, name, "")
        return bool(value)

    def configured_adapters(self) -> list[str]:
        mapping = {
            "campaign": "config",
            "heartbeat": "heartbeat",
            "manifest": "manifest",
            "upload": "upload",
            "physiology": "physiology",
        }
        return [adapter for endpoint, adapter in mapping.items() if self.is_configured(endpoint)]
