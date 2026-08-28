"""Node identifier helpers."""

from __future__ import annotations

from nilo_node.config.models import AppConfig


def node_short_id(node_id: str) -> str:
    """First 8 hex chars of node UUID (matches WiFi SSID suffix)."""
    return node_id.replace("-", "")[:8]


def verify_setup_login(
    *,
    config: AppConfig,
    node_id: str,
    username: str,
    password: str,
) -> bool:
    """Portal login: node uuid8 + WiFi password, or explicit .env credentials."""
    username = username.strip()
    password = password.strip()
    wifi_pass = (config.wifi.password or "").strip()
    if wifi_pass and username == node_short_id(node_id) and password == wifi_pass:
        return True
    env_user = config.local_api.setup_username.strip()
    env_pass = config.local_api.setup_password.strip()
    return bool(env_user and env_pass and username == env_user and password == env_pass)
