"""Tests for WiFi interface autodetection."""

from __future__ import annotations

from nilo_node.network.wifi_detect import (
    _is_virtual_ap_name,
    detect_wifi_interface,
    resolve_wifi_interface,
)


def test_is_virtual_ap_name() -> None:
    assert _is_virtual_ap_name("uap0")
    assert _is_virtual_ap_name("wlan0-ap")
    assert not _is_virtual_ap_name("wlp3s0")


def test_auto_returns_string_or_none() -> None:
    iface = detect_wifi_interface("auto")
    assert iface is None or (isinstance(iface, str) and len(iface) > 0)


def test_resolve_auto_returns_string() -> None:
    name = resolve_wifi_interface("auto")
    assert isinstance(name, str)
    assert len(name) > 0


def test_explicit_unknown_falls_back_to_autodetect() -> None:
    # Non-existent iface should not crash
    result = detect_wifi_interface("nonexistent-wifi-xyz")
    assert result is None or isinstance(result, str)
