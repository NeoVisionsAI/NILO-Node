"""API authentication dependency."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from nilo_node.config.models import AppConfig

_bearer = HTTPBearer(auto_error=False)


def require_auth(config: AppConfig):
    async def _dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        expected = config.local_api.auth_token
        if not expected:
            return
        if credentials is None or credentials.credentials != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token",
            )

    return _dependency
