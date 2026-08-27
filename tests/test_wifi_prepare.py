"""Tests for WiFi AP preparation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nilo_node.network.wifi_prepare import (
    ap_interface_candidates,
    channel_is_dfs,
    freq_to_channel,
    plan_ap_interface,
    resolve_ap_interface_name,
    rtnetlink_error_is_benign,
    verify_ap_mode,
)


def test_freq_to_channel() -> None:
    assert freq_to_channel(2437) == 6
    assert freq_to_channel(5540) == 108


def test_hw_mode_for_channel() -> None:
    from nilo_node.network.wifi_prepare import hw_mode_for_channel

    assert hw_mode_for_channel(6) == "g"
    assert hw_mode_for_channel(108) == "a"


def test_channel_is_dfs() -> None:
    assert channel_is_dfs(108) is True
    assert channel_is_dfs(36) is False
    assert channel_is_dfs(6) is False
    assert channel_is_dfs(52) is True


def test_rtnetlink_benign() -> None:
    assert rtnetlink_error_is_benign("RTNETLINK answers: Name not unique on network")


def test_resolve_ap_interface_name() -> None:
    assert resolve_ap_interface_name("wlp3s0", "auto") == "wlp3s0-ap"
    assert resolve_ap_interface_name("wlp3s0", "uap0") == "uap0"


def test_ap_interface_candidates() -> None:
    names = ap_interface_candidates("wlp3s0", "auto")
    assert names[0] == "wlp3s0-ap"
    assert "uap0" in names


@pytest.mark.asyncio
async def test_plan_concurrent() -> None:
    with patch("nilo_node.network.wifi_prepare._run_cmd", new_callable=AsyncMock) as run_cmd:
        run_cmd.return_value = (0, "ok")
        with patch("nilo_node.network.wifi_prepare.shutil.which") as which:
            which.side_effect = lambda name: {
                "rfkill": "/usr/sbin/rfkill",
                "iw": "/usr/sbin/iw",
            }.get(name)
            with patch(
                "nilo_node.network.wifi_prepare.detect_operating_channel",
                return_value=108,
            ):
                plan = await plan_ap_interface(
                    "wlp3s0",
                    "auto",
                    concurrent_sta_ap=True,
                    country_code="ES",
                    ap_ip="192.168.50.1",
                    netmask_prefix=24,
                    default_channel=6,
                )
    assert plan.mode == "concurrent"
    assert plan.ap_interface == "wlp3s0-ap"
    assert plan.channel == 108
    assert plan.hw_mode == "a"


@pytest.mark.asyncio
async def test_interface_type_parses_ap() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "Interface wlp3s0-ap\n\tifindex 42\n\ttype AP\n"),
    ):
        with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
            from nilo_node.network.wifi_prepare import _interface_type

            assert await _interface_type("wlp3s0-ap") == "AP"


@pytest.mark.asyncio
async def test_create_virtual_ap_accepts_ap_type() -> None:
    with patch(
        "nilo_node.network.wifi_prepare.cleanup_phy_ap_interfaces",
        new_callable=AsyncMock,
    ):
        with patch(
            "nilo_node.network.wifi_prepare.teardown_virtual_ap",
            new_callable=AsyncMock,
        ):
            with patch(
                "nilo_node.network.wifi_prepare.force_remove_iface",
                new_callable=AsyncMock,
            ):
                with patch(
                    "nilo_node.network.wifi_prepare._create_virtual_ap_once",
                    new_callable=AsyncMock,
                    return_value=(True, "ok"),
                ):
                    with patch(
                        "nilo_node.network.wifi_prepare._interface_exists",
                        new_callable=AsyncMock,
                        return_value=True,
                    ):
                        with patch(
                            "nilo_node.network.wifi_prepare._interface_type",
                            new_callable=AsyncMock,
                            return_value="AP",
                        ):
                            from nilo_node.network.wifi_prepare import create_virtual_ap

                            assert await create_virtual_ap("wlp3s0", "auto") == "wlp3s0-ap"


@pytest.mark.asyncio
async def test_verify_ap_mode_ok() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "type AP\nssid nilo-node-abc"),
    ):
        with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
            assert await verify_ap_mode("wlp3s0-ap") is None
