"""JWT authentication: login, refresh, and access-token lifecycle."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

from nilo_node.backend.auth.jwt_utils import jwt_expires_at
from nilo_node.backend.auth.models import TokenSet
from nilo_node.backend.auth.store import TokenStore
from nilo_node.backend.exceptions import BackendAuthError, BackendEndpointNotConfiguredError
from nilo_node.config.models import AppConfig

if TYPE_CHECKING:
    from nilo_node.backend.endpoints import BackendEndpoints

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages JWT acquisition, refresh, and Authorization headers."""

    def __init__(
        self,
        config: AppConfig,
        token_store: TokenStore,
        node_id: str,
    ) -> None:
        self._config = config
        self._store = token_store
        self._node_id = node_id
        self._lock = asyncio.Lock()
        self._tokens: TokenSet | None = self._store.load()

    @property
    def is_jwt_mode(self) -> bool:
        return self._config.backend.auth.mode == "jwt"

    @property
    def authenticated(self) -> bool:
        if self._config.backend.auth.mode == "api_key":
            return bool(self._config.backend.api_key or self._config.backend.auth.client_secret)
        if self._config.backend.auth.mode == "jwt":
            return self._tokens is not None and not self._tokens.is_expired(
                self._config.backend.auth.refresh_skew_sec
            )
        return True

    async def initialize(self, client: httpx.AsyncClient) -> None:
        if not self.is_jwt_mode:
            return

        self._tokens = self._store.load()
        if self._tokens and self._tokens.expires_at is None and self._tokens.access_token:
            exp = jwt_expires_at(self._tokens.access_token)
            if exp:
                self._tokens.expires_at = exp

        if self._tokens and not self._tokens.is_expired(self._config.backend.auth.refresh_skew_sec):
            logger.info("Loaded valid access token from store")
            return

        if self._tokens and self._tokens.refresh_token:
            try:
                await self.refresh(client)
                return
            except BackendAuthError:
                logger.warning("Stored refresh token invalid, performing fresh login")

        await self.login(client)

    async def get_authorization_header(self, client: httpx.AsyncClient) -> dict[str, str]:
        mode = self._config.backend.auth.mode
        if mode == "none":
            return {}
        if mode == "api_key":
            key = self._config.backend.api_key or self._config.backend.auth.client_secret
            if key:
                return {"Authorization": f"Bearer {key}"}
            return {}
        if mode == "jwt":
            token = await self.get_access_token(client)
            token_type = self._tokens.token_type if self._tokens else "Bearer"
            return {"Authorization": f"{token_type} {token}"}
        return {}

    async def get_access_token(self, client: httpx.AsyncClient) -> str:
        if not self.is_jwt_mode:
            raise BackendAuthError("JWT mode is not enabled")
        async with self._lock:
            if self._tokens is None or self._tokens.is_expired(
                self._config.backend.auth.refresh_skew_sec
            ):
                if self._tokens and self._tokens.refresh_token:
                    await self._refresh_locked(client)
                else:
                    await self._login_locked(client)
            assert self._tokens is not None
            return self._tokens.access_token

    async def login(self, client: httpx.AsyncClient) -> TokenSet:
        async with self._lock:
            return await self._login_locked(client)

    async def refresh(self, client: httpx.AsyncClient) -> TokenSet:
        async with self._lock:
            return await self._refresh_locked(client)

    async def invalidate(self) -> None:
        async with self._lock:
            self._tokens = None
            self._store.clear()

    async def _login_locked(self, client: httpx.AsyncClient) -> TokenSet:
        endpoints: BackendEndpoints = self._config.backend.endpoints
        if not endpoints.login:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.login is not configured"
            )

        url = endpoints.resolve(endpoints.login, node_id=self._node_id)
        body = self._build_login_body()
        logger.info("Authenticating with NILO-backend: POST %s", url)

        response = await client.post(url, json=body)
        if response.status_code >= 400:
            raise BackendAuthError(
                f"Login failed: HTTP {response.status_code} — {response.text[:200]}"
            )

        tokens = TokenSet.from_response(response.json())
        if tokens.expires_at is None:
            tokens.expires_at = jwt_expires_at(tokens.access_token)

        self._tokens = tokens
        self._store.save(tokens)
        logger.info("Authentication successful (expires_at=%s)", tokens.expires_at)
        return tokens

    async def _refresh_locked(self, client: httpx.AsyncClient) -> TokenSet:
        endpoints = self._config.backend.endpoints
        if not endpoints.refresh:
            logger.warning("Refresh endpoint not configured, falling back to login")
            return await self._login_locked(client)

        if self._tokens is None or not self._tokens.refresh_token:
            return await self._login_locked(client)

        url = endpoints.resolve(endpoints.refresh, node_id=self._node_id)
        body = {
            "refresh_token": self._tokens.refresh_token,
            "grant_type": "refresh_token",
        }
        logger.info("Refreshing access token: POST %s", url)

        response = await client.post(url, json=body)
        if response.status_code >= 400:
            raise BackendAuthError(
                f"Token refresh failed: HTTP {response.status_code}"
            )

        tokens = TokenSet.from_response(response.json())
        if tokens.refresh_token is None:
            tokens.refresh_token = self._tokens.refresh_token
        if tokens.expires_at is None:
            tokens.expires_at = jwt_expires_at(tokens.access_token)

        self._tokens = tokens
        self._store.save(tokens)
        logger.info("Token refresh successful (expires_at=%s)", tokens.expires_at)
        return tokens

    def _build_login_body(self) -> dict[str, Any]:
        auth = self._config.backend.auth
        if auth.login_grant == "client_credentials":
            return {
                "grant_type": "client_credentials",
                "client_id": auth.client_id or self._node_id,
                "client_secret": auth.client_secret or self._config.backend.api_key,
            }
        return {
            "grant_type": "password",
            "node_id": self._node_id,
            "client_id": auth.client_id or self._node_id,
            "client_secret": auth.client_secret or self._config.backend.api_key,
        }
