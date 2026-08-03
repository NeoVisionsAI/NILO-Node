"""Load and validate YAML configuration with environment substitution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from nilo_node.config.models import AppConfig

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            key = match.group(1)
            return os.environ.get(key, "")

        return _ENV_PATTERN.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    return value


def load_config(path: str | Path) -> AppConfig:
    """Load YAML config file, substitute env vars, validate with pydantic."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    resolved = _substitute_env(raw)
    return AppConfig.model_validate(resolved)
