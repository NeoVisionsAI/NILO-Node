"""Tests for persisted OAK connection settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from nilo_node.camera.oak_settings import (
    OakConnectionSettings,
    load_oak_connection_settings,
    save_oak_connection_settings,
)


def test_load_from_oak_local_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "oak.local.yaml"
    config.write_text(
        """
camera:
  device_ip: "169.254.1.222"
  connection_mode: poe
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OAK_CONFIG_PATH", str(config))
    monkeypatch.delenv("OAK_DEVICE_IP", raising=False)

    settings = load_oak_connection_settings()
    assert settings.device_ip == "169.254.1.222"
    assert settings.connection_mode == "poe"


def test_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "oak.local.yaml"
    config.write_text("camera:\n  device_ip: '169.254.1.222'\n", encoding="utf-8")
    monkeypatch.setenv("OAK_CONFIG_PATH", str(config))
    monkeypatch.setenv("OAK_DEVICE_IP", "10.0.0.5")

    settings = load_oak_connection_settings()
    assert settings.device_ip == "10.0.0.5"


def test_explicit_args_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OAK_DEVICE_IP", "10.0.0.5")
    settings = load_oak_connection_settings(device_ip="192.168.1.50")
    assert settings.device_ip == "192.168.1.50"


def test_save_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "oak.local.yaml"
    settings = OakConnectionSettings(device_ip="169.254.1.222", connection_mode="poe")
    save_oak_connection_settings(settings, path=target)
    mtime = target.stat().st_mtime
    save_oak_connection_settings(settings, path=target)
    assert target.stat().st_mtime == mtime
    assert "169.254.1.222" in target.read_text(encoding="utf-8")
