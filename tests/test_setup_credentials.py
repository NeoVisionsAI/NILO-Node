"""Tests for setup portal login credentials."""

from __future__ import annotations

from nilo_node.config.models import AppConfig
from nilo_node.util.node_id import node_short_id, verify_setup_login


def _config(wifi_pass: str = "nilo2026", setup_user: str = "", setup_pass: str = "") -> AppConfig:
    return AppConfig.model_validate(
        {
            "storage": {"base_path": "/tmp/nilo"},
            "local_api": {
                "auth_token": "token",
                "setup_username": setup_user,
                "setup_password": setup_pass,
            },
            "wifi": {"enabled": True, "password": wifi_pass},
            "backend": {"enabled": False},
        }
    )


def test_node_short_id() -> None:
    assert node_short_id("1f94bda0-1234-5678-9abc-def012345678") == "1f94bda0"
    assert node_short_id("node-abc-123") == "nodeabc1"


def test_verify_setup_login_wifi_defaults() -> None:
    cfg = _config(wifi_pass="nilo2026")
    assert verify_setup_login(
        config=cfg,
        node_id="1f94bda0-1234-5678-9abc-def012345678",
        username="1f94bda0",
        password="nilo2026",
    )
    assert not verify_setup_login(
        config=cfg,
        node_id="1f94bda0-1234-5678-9abc-def012345678",
        username="admin",
        password="nilo2026",
    )


def test_verify_setup_login_explicit_env() -> None:
    cfg = _config(wifi_pass="wifi", setup_user="admin", setup_pass="portal-pass")
    assert verify_setup_login(
        config=cfg,
        node_id="1f94bda0-0000-0000-0000-000000000000",
        username="admin",
        password="portal-pass",
    )
