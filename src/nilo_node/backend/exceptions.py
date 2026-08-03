"""Backend integration exceptions."""

from __future__ import annotations


class BackendError(Exception):
    """Base error for NILO-backend integration."""


class BackendAuthError(BackendError):
    """Authentication or token refresh failed."""


class BackendEndpointNotConfiguredError(BackendError):
    """Required endpoint path is not configured yet."""


class BackendRequestError(BackendError):
    """HTTP request failed after retries."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
