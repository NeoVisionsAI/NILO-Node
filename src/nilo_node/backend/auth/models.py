"""Authentication domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class TokenSet(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    scope: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self, skew_sec: int = 0) -> bool:
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now.timestamp() >= (expires.timestamp() - skew_sec)

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> TokenSet:
        """Parse common JWT login/refresh response shapes."""
        access = (
            payload.get("access_token")
            or payload.get("accessToken")
            or payload.get("token")
        )
        if not access:
            raise ValueError("Response missing access token field")

        refresh = payload.get("refresh_token") or payload.get("refreshToken")
        token_type = payload.get("token_type") or payload.get("tokenType") or "Bearer"
        expires_at: datetime | None = None

        expires_in = payload.get("expires_in") or payload.get("expiresIn")
        if expires_in is not None:
            expires_at = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + float(expires_in),
                tz=timezone.utc,
            )

        exp = payload.get("exp")
        if exp is not None and expires_at is None:
            expires_at = datetime.fromtimestamp(float(exp), tz=timezone.utc)

        return cls(
            access_token=str(access),
            refresh_token=str(refresh) if refresh else None,
            token_type=str(token_type),
            expires_at=expires_at,
            scope=payload.get("scope"),
            raw=payload,
        )
