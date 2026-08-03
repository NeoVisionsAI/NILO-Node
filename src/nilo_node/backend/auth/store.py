"""Persistent token storage."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from nilo_node.backend.auth.models import TokenSet

logger = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> TokenSet | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("expires_at"):
                data["expires_at"] = datetime.fromisoformat(data["expires_at"])
            return TokenSet.model_validate(data)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to load token store %s: %s", self._path, exc)
            return None

    def save(self, tokens: TokenSet) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = tokens.model_dump(mode="json")
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        logger.debug("Saved token store to %s", self._path)

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
            logger.info("Cleared token store %s", self._path)
