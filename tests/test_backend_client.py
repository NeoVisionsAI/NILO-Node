"""Tests for backend client, transport, and adapters."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from nilo_node.backend.client import BackendClient
from nilo_node.backend.transport import BackendTransport
from nilo_node.backend.auth.manager import AuthManager
from nilo_node.backend.auth.store import TokenStore
from nilo_node.config.models import AppConfig


CAMPAIGN_JSON = {
    "campaign_id": "camp-remote",
    "campaign_name": "pruebas_dolor",
    "subject_user_id": "patient-99",
    "status": "active",
    "schedule": {"mode": "always"},
}


@pytest.mark.asyncio
async def test_backend_client_fetch_campaign_with_jwt(tmp_path: Path) -> None:
    request_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        request_count["n"] += 1
        auth = request.headers.get("Authorization", "")
        if request.url.path == "/auth/login":
            return httpx.Response(
                200,
                json={"access_token": "tok", "refresh_token": "ref", "expires_in": 3600},
            )
        if request.url.path == "/nodes/node-abc/campaign":
            if not auth.startswith("Bearer "):
                return httpx.Response(401)
            return httpx.Response(200, json=CAMPAIGN_JSON)
        return httpx.Response(404)

    config = AppConfig.model_validate(
        {
            "backend": {
                "enabled": True,
                "base_url": "https://api.test",
                "auth": {
                    "mode": "jwt",
                    "client_id": "id",
                    "client_secret": "secret",
                    "token_store_path": "auth_tokens.json",
                },
                "endpoints": {
                    "login": "/auth/login",
                    "refresh": "/auth/refresh",
                    "campaign": "/nodes/{node_id}/campaign",
                    "heartbeat": "/nodes/{node_id}/heartbeat",
                },
                "adapters": {"config": {"enabled": True}, "heartbeat": {"enabled": True}},
            },
            "storage": {"base_path": str(tmp_path)},
            "monitoring": {"dev_campaign": None},
        }
    )

    transport = httpx.MockTransport(handler)
    client = BackendClient(config, tmp_path, "node-abc")

    original_transport_class = BackendTransport

    class PatchedTransport(original_transport_class):
        async def start(self) -> None:
            self._client = httpx.AsyncClient(
                transport=transport,
                base_url=config.backend.base_url,
                timeout=httpx.Timeout(config.backend.request_timeout_sec),
            )
            if self._auth.is_jwt_mode and config.backend.endpoints.login:
                await self._auth.initialize(self._client)

    client._transport = PatchedTransport(config, client._auth, "node-abc")
    client._started = False

    await client.start()
    campaign = await client.fetch_campaign()

    assert campaign is not None
    assert campaign.campaign_name == "pruebas_dolor"
    assert campaign.subject_user_id == "patient-99"
    assert client.connectivity.consecutive_failures == 0
    await client.close()


@pytest.mark.asyncio
async def test_backend_client_uses_dev_campaign_override(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "backend": {"enabled": True},
            "storage": {"base_path": str(tmp_path)},
            "monitoring": {
                "dev_campaign": {
                    "campaign_id": "dev",
                    "campaign_name": "local",
                    "subject_user_id": None,
                    "status": "active",
                    "schedule": {"mode": "always"},
                }
            },
        }
    )
    client = BackendClient(config, tmp_path, "node-1")
    campaign = await client.fetch_campaign()
    assert campaign is not None
    assert campaign.campaign_name == "local"


@pytest.mark.asyncio
async def test_transport_retries_on_401_with_refresh(tmp_path: Path) -> None:
    state = {"access": "old-token", "refreshed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/refresh":
            state["refreshed"] = True
            state["access"] = "new-token"
            return httpx.Response(
                200,
                json={"access_token": "new-token", "expires_in": 3600},
            )
        if request.url.path == "/protected":
            auth = request.headers.get("Authorization", "")
            if auth == "Bearer new-token":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(401)
        return httpx.Response(404)

    config = AppConfig.model_validate(
        {
            "backend": {
                "base_url": "https://api.test",
                "auth": {"mode": "jwt"},
                "endpoints": {"refresh": "/auth/refresh"},
                "retry": {"max_attempts": 3, "backoff_sec": 0.01},
            },
            "storage": {"base_path": str(tmp_path)},
        }
    )
    store = TokenStore(tmp_path / "tokens.json")
    store.save(
        __import__("nilo_node.backend.auth.models", fromlist=["TokenSet"]).TokenSet(
            access_token="old-token",
            refresh_token="refresh-1",
        )
    )
    auth = AuthManager(config, store, "node-1")
    transport = BackendTransport(config, auth, "node-1")

    transport._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.test",
    )

    result = await transport.get_json("/protected")
    assert result == {"ok": True}
    assert state["refreshed"] is True
    await transport.close()
