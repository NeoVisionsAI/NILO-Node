"""Load and persist local OAK connection settings (PoE IP, mode, device id)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_OAK_LOCAL_REL = Path("config/oak.local.yaml")
DEFAULT_NILO_CONFIG_REL = Path("config/nilo-node.yaml")


@dataclass(frozen=True)
class OakConnectionSettings:
    device_ip: str = ""
    device_id: str = ""
    connection_mode: str = "auto"


def find_repo_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for pyproject.toml."""
    current = (start or Path(__file__).resolve()).resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def default_oak_config_paths() -> list[Path]:
    """Config files checked in order (later entries override earlier ones)."""
    paths: list[Path] = []

    repo = find_repo_root()
    if repo is not None:
        paths.append(repo / DEFAULT_NILO_CONFIG_REL)
        paths.append(repo / DEFAULT_OAK_LOCAL_REL)

    paths.extend(
        [
            Path("/opt/nilo-node/config/nilo-node.yaml"),
            Path("/opt/nilo-node/config/oak.local.yaml"),
            Path("/etc/nilo-node/nilo-node.yaml"),
        ]
    )

    if env := os.environ.get("NILO_CONFIG_PATH", "").strip():
        paths.append(Path(env))

    if env := os.environ.get("OAK_CONFIG_PATH", "").strip():
        paths.append(Path(env))

    # De-duplicate while preserving order (last wins on merge).
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def default_oak_local_path() -> Path:
    """Preferred path for writing discovered/local OAK settings."""
    if env := os.environ.get("OAK_CONFIG_PATH", "").strip():
        return Path(env).expanduser()

    install = Path("/opt/nilo-node/config/oak.local.yaml")
    if install.parent.is_dir():
        return install

    repo = find_repo_root()
    if repo is not None:
        return repo / DEFAULT_OAK_LOCAL_REL

    return Path.cwd() / DEFAULT_OAK_LOCAL_REL


def _camera_section(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    camera = raw.get("camera")
    if isinstance(camera, dict):
        return camera
    return raw


def _read_settings_file(path: Path) -> OakConnectionSettings:
    if not path.is_file():
        return OakConnectionSettings()

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        logger.debug("Could not read OAK config %s: %s", path, exc)
        return OakConnectionSettings()
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in OAK config %s: %s", path, exc)
        return OakConnectionSettings()

    section = _camera_section(raw)
    return OakConnectionSettings(
        device_ip=str(section.get("device_ip") or "").strip(),
        device_id=str(section.get("device_id") or "").strip(),
        connection_mode=str(section.get("connection_mode") or "auto").strip() or "auto",
    )


def load_oak_connection_settings(
    *,
    device_ip: str | None = None,
    device_id: str | None = None,
    prefer: str | None = None,
    extra_paths: list[Path] | None = None,
) -> OakConnectionSettings:
    """Resolve OAK settings: files (low) → env → explicit args (high)."""
    merged = OakConnectionSettings()

    for path in default_oak_config_paths():
        file_settings = _read_settings_file(path)
        merged = _merge_settings(merged, file_settings)

    if extra_paths:
        for path in extra_paths:
            merged = _merge_settings(merged, _read_settings_file(path))

    env_settings = OakConnectionSettings(
        device_ip=os.environ.get("OAK_DEVICE_IP", "").strip(),
        device_id=os.environ.get("OAK_DEVICE_ID", "").strip(),
        connection_mode=os.environ.get("OAK_CONNECTION", "").strip() or "auto",
    )
    merged = _merge_settings(merged, env_settings)

    explicit = OakConnectionSettings(
        device_ip=(device_ip or "").strip(),
        device_id=(device_id or "").strip(),
        connection_mode=(prefer or "").strip(),
    )
    return _merge_settings(merged, explicit)


def _merge_settings(base: OakConnectionSettings, override: OakConnectionSettings) -> OakConnectionSettings:
    mode = override.connection_mode if override.connection_mode not in ("", "auto") else base.connection_mode
    return OakConnectionSettings(
        device_ip=override.device_ip or base.device_ip,
        device_id=override.device_id or base.device_id,
        connection_mode=mode or "auto",
    )


def save_oak_connection_settings(
    settings: OakConnectionSettings,
    *,
    path: Path | None = None,
) -> Path:
    """Write local OAK settings (gitignored `config/oak.local.yaml` by default)."""
    target = (path or default_oak_local_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "camera": {
            "device_ip": settings.device_ip,
            "device_id": settings.device_id,
            "connection_mode": settings.connection_mode,
        }
    }

    existing = _read_settings_file(target)
    if (
        existing.device_ip == settings.device_ip
        and existing.device_id == settings.device_id
        and existing.connection_mode == settings.connection_mode
    ):
        logger.debug("OAK settings unchanged in %s", target)
        return target

    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False, allow_unicode=True)

    logger.info(
        "Saved OAK settings to %s (ip=%s, mode=%s)",
        target,
        settings.device_ip or "-",
        settings.connection_mode,
    )
    return target


def persist_discovered_oak_settings(meta: dict[str, str], *, connection_mode: str = "poe") -> Path | None:
    """Store PoE IP / device id after a successful discovery or connect."""
    ip = (meta.get("ip") or "").strip()
    device_id = (meta.get("mxid") or "").strip()
    mode = (meta.get("connection") or connection_mode or "poe").strip()

    if not ip and not device_id:
        return None

    try:
        return save_oak_connection_settings(
            OakConnectionSettings(
                device_ip=ip,
                device_id=device_id if device_id != ip else "",
                connection_mode=mode if mode in ("auto", "usb", "poe") else "poe",
            )
        )
    except OSError as exc:
        logger.warning("Could not persist OAK settings: %s", exc)
        return None
