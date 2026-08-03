"""HTTP transport with auth, retry, logging, and 401 refresh."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from nilo_node.backend.auth.manager import AuthManager
from nilo_node.backend.exceptions import BackendAuthError, BackendRequestError
from nilo_node.config.models import AppConfig

logger = logging.getLogger(__name__)
http_logger = logging.getLogger("nilo_node.backend.http")


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "Authorization" in redacted:
        value = redacted["Authorization"]
        if len(value) > 20:
            redacted["Authorization"] = value[:12] + "…(redacted)"
        else:
            redacted["Authorization"] = "(redacted)"
    return redacted


class BackendTransport:
    """Async HTTP client for NILO-backend with JWT and retry support."""

    def __init__(self, config: AppConfig, auth: AuthManager, node_id: str) -> None:
        self._config = config
        self._auth = auth
        self._node_id = node_id
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._config.backend.base_url.rstrip("/"),
            timeout=httpx.Timeout(self._config.backend.request_timeout_sec),
            headers={"Accept": "application/json", "User-Agent": "NILO-Node/0.1"},
        )
        if self._auth.is_jwt_mode and self._config.backend.endpoints.login:
            await self._auth.initialize(self._client)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BackendTransport not started")
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        data: Any = None,
        files: Any = None,
        params: dict[str, Any] | None = None,
        skip_auth: bool = False,
        retry_on_unauthorized: bool = True,
    ) -> httpx.Response:
        formatted_path = path.format(node_id=self._node_id)
        attempts = self._config.backend.retry.max_attempts
        backoff = self._config.backend.retry.backoff_sec
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            headers = {}
            if not skip_auth:
                headers.update(await self._auth.get_authorization_header(self.client))

            start = time.perf_counter()
            try:
                response = await self.client.request(
                    method,
                    formatted_path,
                    json=json,
                    data=data,
                    files=files,
                    params=params,
                    headers=headers,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                http_logger.info(
                    "HTTP %s %s → %s (%.0fms)",
                    method,
                    formatted_path,
                    response.status_code,
                    elapsed_ms,
                )

                if response.status_code == 401 and retry_on_unauthorized and not skip_auth:
                    if self._auth.is_jwt_mode:
                        http_logger.warning("HTTP 401 — attempting token refresh")
                        try:
                            await self._auth.refresh(self.client)
                        except BackendAuthError:
                            await self._auth.login(self.client)
                        continue

                if response.status_code >= 500 and attempt < attempts:
                    await asyncio.sleep(backoff * attempt)
                    continue

                return response

            except httpx.RequestError as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                http_logger.warning(
                    "HTTP %s %s failed (%.0fms): %s",
                    method,
                    formatted_path,
                    elapsed_ms,
                    exc,
                )
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(backoff * attempt)
                    continue
                raise BackendRequestError(f"Request failed: {exc}") from exc

        if last_error:
            raise BackendRequestError(f"Request failed after {attempts} attempts") from last_error
        raise BackendRequestError("Request failed")

    async def get_json(self, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.request("GET", path, **kwargs)
        if response.status_code >= 400:
            raise BackendRequestError(
                f"GET {path} failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        response = await self.request("POST", path, json=payload, **kwargs)
        if response.status_code >= 400:
            raise BackendRequestError(
                f"POST {path} failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()
