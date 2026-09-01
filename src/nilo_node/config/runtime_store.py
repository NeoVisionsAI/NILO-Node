"""Persistent runtime settings (SQLite + YAML mirror) editable from setup portal / MQTT."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from nilo_node.state.database import Database

logger = logging.getLogger(__name__)


class RuntimeSettings(BaseModel):
    """User-editable device settings applied at runtime."""

    camera: dict[str, Any] = Field(default_factory=dict)
    wifi: dict[str, Any] = Field(default_factory=dict)
    bluetooth: dict[str, Any] = Field(default_factory=dict)
    mqtt: dict[str, Any] = Field(default_factory=dict)
    monitoring: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None


class RuntimeSettingsStore:
    def __init__(self, db: Database, storage_base: Path) -> None:
        self._db = db
        self._yaml_path = storage_base / "config" / "runtime-settings.yaml"

    def load(self) -> RuntimeSettings:
        conn = self._db.connect()
        row = conn.execute("SELECT payload FROM runtime_settings WHERE id = 1").fetchone()
        if row is None:
            return RuntimeSettings()
        try:
            data = json.loads(row["payload"])
            return RuntimeSettings.model_validate(data)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Invalid runtime settings in DB: %s", exc)
            return RuntimeSettings()

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        settings.updated_at = datetime.now(timezone.utc).isoformat()
        payload = settings.model_dump(mode="json")
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO runtime_settings (id, payload, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (json.dumps(payload), settings.updated_at),
        )
        conn.commit()
        self._write_yaml(payload)
        logger.info("Runtime settings saved")
        return settings

    def merge_and_save(self, patch: dict[str, Any]) -> RuntimeSettings:
        current = self.load()
        data = current.model_dump()
        for section in ("camera", "wifi", "bluetooth", "mqtt", "monitoring"):
            if section in patch and isinstance(patch[section], dict):
                section_data = dict(data.get(section) or {})
                section_data.update({k: v for k, v in patch[section].items() if v is not None})
                data[section] = section_data
        updated = RuntimeSettings.model_validate(data)
        return self.save(updated)

    def _write_yaml(self, payload: dict[str, Any]) -> None:
        self._yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with self._yaml_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)
