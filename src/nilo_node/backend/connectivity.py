"""Backend connectivity state tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ConnectivityState:
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    authenticated: bool = False

    def record_success(self) -> None:
        self.last_success_at = datetime.now(timezone.utc)
        self.last_failure_at = None
        self.last_error = None
        self.consecutive_failures = 0

    def record_failure(self, error: str) -> None:
        self.last_failure_at = datetime.now(timezone.utc)
        self.last_error = error
        self.consecutive_failures += 1

    def is_within_grace(self, grace_sec: int) -> bool:
        if self.last_success_at is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.last_success_at).total_seconds()
        return elapsed <= grace_sec

    def to_dict(self) -> dict:
        return {
            "authenticated": self.authenticated,
            "last_success_at": (
                self.last_success_at.isoformat() if self.last_success_at else None
            ),
            "last_failure_at": (
                self.last_failure_at.isoformat() if self.last_failure_at else None
            ),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "within_offline_grace": False,
        }
