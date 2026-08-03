"""JWT helpers without signature verification (expiry only)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload (no signature verification)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    payload_b64 = parts[1]
    padding = "=" * (-len(payload_b64) % 4)
    decoded = base64.urlsafe_b64decode(payload_b64 + padding)
    return json.loads(decoded.decode("utf-8"))


def jwt_expires_at(token: str) -> datetime | None:
    try:
        payload = decode_jwt_payload(token)
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    if exp is None:
        return None
    return datetime.fromtimestamp(float(exp), tz=timezone.utc)
