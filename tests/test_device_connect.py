"""Tests for OAK PoE / DepthAI device connection helpers."""

from __future__ import annotations

from nilo_node.camera.device_connect import (
    DEFAULT_POE_CAMERA_IP,
    device_info_for_ip,
    should_use_depthai_hardware,
    uses_depthai_v2,
)
from nilo_node.config.models import CameraConfig


class _FakeDaiV3:
    class node:
        pass

    class DeviceInfo:
        def __init__(self, device_id: str) -> None:
            self.device_id = device_id


class _FakeDaiV2(_FakeDaiV3):
    class node(_FakeDaiV3.node):
        class XLinkOut:
            pass

    class XLinkProtocol:
        X_LINK_TCP_IP = 4

    class DeviceInfo:
        def __init__(self, device_id: str, protocol: int | None = None) -> None:
            self.device_id = device_id
            self.protocol = protocol


def test_uses_depthai_v2_detection() -> None:
    assert uses_depthai_v2(_FakeDaiV2()) is True
    assert uses_depthai_v2(_FakeDaiV3()) is False


def test_device_info_for_ip_v3_string_only() -> None:
    info = device_info_for_ip(_FakeDaiV3(), DEFAULT_POE_CAMERA_IP)
    assert info.device_id == DEFAULT_POE_CAMERA_IP
    assert not hasattr(info, "protocol") or info.protocol is None


def test_device_info_for_ip_v2_includes_protocol() -> None:
    info = device_info_for_ip(_FakeDaiV2(), DEFAULT_POE_CAMERA_IP)
    assert info.device_id == DEFAULT_POE_CAMERA_IP
    assert info.protocol == 4


def test_should_use_depthai_with_poe_config_without_discover() -> None:
    cfg = CameraConfig(
        device_ip="169.254.1.222",
        connection_mode="poe",
        mock_when_unavailable=False,
    )
    assert should_use_depthai_hardware(
        depthai_ok=True,
        devices=[],
        device_ip=cfg.device_ip,
        connection_mode=cfg.connection_mode,
    )


def test_should_use_depthai_poe_mode_without_ip() -> None:
    assert should_use_depthai_hardware(
        depthai_ok=True,
        devices=[],
        device_ip="",
        connection_mode="poe",
    )


def test_should_not_use_depthai_without_discover_or_poe() -> None:
    assert not should_use_depthai_hardware(
        depthai_ok=True,
        devices=[],
        device_ip="",
        connection_mode="auto",
    )
