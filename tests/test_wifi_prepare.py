"""Tests for WiFi AP preparation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nilo_node.network.wifi_prepare import (
    _create_virtual_ap,
    freq_to_channel,
    plan_ap_interface,
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


def test_rtnetlink_benign() -> None:
    assert rtnetlink_error_is_benign("RTNETLINK answers: Name not unique on network")


@pytest.mark.asyncio
async def test_create_virtual_ap_recovers_from_name_not_unique() -> None:
    with patch("nilo_node.network.wifi_prepare._run_cmd", new_callable=AsyncMock) as run_cmd:
        with patch(
            "nilo_node.network.wifi_prepare._interface_exists",
            new_callable=AsyncMock,
            side_effect=[False, True, False, True],
        ):
            with patch(
                "nilo_node.network.wifi_prepare.teardown_virtual_ap",
                new_callable=AsyncMock,
            ):
                run_cmd.return_value = (2, "RTNETLINK answers: Name not unique on network")
                with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
                    assert await _create_virtual_ap("wlp3s0", "uap0") is True


@pytest.mark.asyncio
async def test_plan_concurrent() -> None:
    with patch("nilo_node.network.wifi_prepare._run_cmd", new_callable=AsyncMock) as run_cmd:
        with patch(
            "nilo_node.network.wifi_prepare._interface_exists",
            new_callable=AsyncMock,
            side_effect=[False, False, True],
        ):
            with patch("nilo_node.network.wifi_prepare.teardown_virtual_ap", new_callable=AsyncMock):
                with patch(
                    "nilo_node.network.wifi_prepare.configure_ap_interface_ip",
                    new_callable=AsyncMock,
                ):
                    run_cmd.return_value = (0, "ok")
                    with patch("nilo_node.network.wifi_prepare.shutil.which") as which:
                        which.side_effect = lambda name: {
                            "rfkill": "/usr/sbin/rfkill",
                            "iw": "/usr/sbin/iw",
                            "nmcli": "/usr/bin/nmcli",
                        }.get(name)
                        with patch(
                            "nilo_node.network.wifi_prepare.detect_operating_channel",
                            return_value=11,
                        ):
                            plan = await plan_ap_interface(
                                "wlp3s0",
                                "uap0",
                                concurrent_sta_ap=True,
                                country_code="ES",
                                ap_ip="192.168.50.1",
                                netmask_prefix=24,
                                default_channel=6,
                            )
    assert plan.mode == "concurrent"
    assert plan.channel == 11


@pytest.mark.asyncio
async def test_verify_ap_mode_ok() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "type AP\nssid nilo-node-abc"),
    ):
        with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
            assert await verify_ap_mode("uap0") is None
