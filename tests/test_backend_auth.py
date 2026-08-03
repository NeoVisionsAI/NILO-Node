"""Tests for JWT auth models and manager."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from nilo_node.backend.auth.jwt_utils import decode_jwt_payload, jwt_expires_at
from nilo_node.backend.auth.manager import AuthManager
from nilo_node.backend.auth.models import TokenSet
from nilo_node.backend.auth.store import TokenStore
from nilo_node.config.models import AppConfig


def _make_jwt(exp: int) -> str:
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": exp}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_token_set_from_response_with_expires_in() -> None:
    tokens = TokenSet.from_response(
        {
            "access_token": "abc",
            "refresh_token": "ref",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )
    assert tokens.access_token == "abc"
    assert tokens.refresh_token == "ref"
    assert tokens.expires_at is not None
    assert not tokens.is_expired(skew_sec=0)


def test_token_set_is_expired() -> None:
    past = datetime.now(timezone.utc).replace(year=2020)
    tokens = TokenSet(access_token="x", expires_at=past)
    assert tokens.is_expired() is True


def test_jwt_expires_at_from_token() -> None:
    exp = int(datetime.now(timezone.utc).timestamp()) + 7200
    token = _make_jwt(exp)
    result = jwt_expires_at(token)
    assert result is not None
    assert abs(result.timestamp() - exp) < 1


def test_decode_jwt_payload() -> None:
    exp = 9999999999
    token = _make_jwt(exp)
    payload = decode_jwt_payload(token)
    assert payload["exp"] == exp


@pytest.mark.asyncio
async def test_auth_manager_login_and_refresh(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/auth/login":
            return httpx.Response(
                200,
                json={
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/auth/refresh":
            return httpx.Response(
                200,
                json={"access_token": "access-2", "expires_in": 3600},
            )
        return httpx.Response(404)

    config = AppConfig.model_validate(
        {
            "backend": {
                "base_url": "https://api.test",
                "auth": {"mode": "jwt", "client_id": "node-1", "client_secret": "secret"},
                "endpoints": {
                    "login": "/auth/login",
                    "refresh": "/auth/refresh",
                },
            },
            "storage": {"base_path": str(tmp_path)},
        }
    )
    store = TokenStore(tmp_path / "tokens.json")
    auth = AuthManager(config, store, "node-1")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.test",
    ) as client:
        tokens = await auth.login(client)
        assert tokens.access_token == "access-1"
        header = await auth.get_authorization_header(client)
        assert header["Authorization"] == "Bearer access-1"

        refreshed = await auth.refresh(client)
        assert refreshed.access_token == "access-2"

    assert "POST /auth/login" in calls
    assert "POST /auth/refresh" in calls
    assert store.load() is not None


@pytest.mark.asyncio
async def test_auth_manager_loads_token_from_store(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "tokens.json")
    exp = int(datetime.now(timezone.utc).timestamp()) + 7200
    store.save(
        TokenSet(
            access_token=_make_jwt(exp),
            refresh_token="refresh-1",
            expires_at=datetime.fromtimestamp(exp, tz=timezone.utc),
        )
    )

    config = AppConfig.model_validate(
        {
            "backend": {
                "base_url": "https://api.test",
                "auth": {"mode": "jwt"},
                "endpoints": {"login": "/auth/login"},
            },
            "storage": {"base_path": str(tmp_path)},
        }
    )
    auth = AuthManager(config, store, "node-1")

    async with httpx.AsyncClient(base_url="https://api.test") as client:
        await auth.initialize(client)

    assert auth.authenticated is True
