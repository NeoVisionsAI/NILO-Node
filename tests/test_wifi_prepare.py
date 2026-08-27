"""Tests for WiFi AP preparation helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nilo_node.network.wifi_prepare import plan_ap_interface, verify_ap_mode


@pytest.mark.asyncio
async def test_plan_concurrent_creates_uap0() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
    ) as run_cmd:
        with patch(
            "nilo_node.network.wifi_prepare._interface_exists",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ):
            run_cmd.return_value = (0, "ok")
            with patch("nilo_node.network.wifi_prepare.shutil.which") as which:
                which.side_effect = lambda name: {
                    "rfkill": "/usr/sbin/rfkill",
                    "iw": "/usr/sbin/iw",
                    "nmcli": "/usr/bin/nmcli",
                    "ip": "/usr/sbin/ip",
                }.get(name)
                plan = await plan_ap_interface(
                    "wlp3s0",
                    "uap0",
                    concurrent_sta_ap=True,
                    country_code="ES",
                )
    assert plan.mode == "concurrent"
    assert plan.sta_interface == "wlp3s0"
    assert plan.ap_interface == "uap0"


@pytest.mark.asyncio
async def test_plan_dedicated_when_virtual_ap_fails() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
    ) as run_cmd:
        with patch(
            "nilo_node.network.wifi_prepare._interface_exists",
            new_callable=AsyncMock,
            return_value=False,
        ):
            run_cmd.return_value = (1, "fail")
            with patch("nilo_node.network.wifi_prepare.shutil.which") as which:
                which.side_effect = lambda name: {
                    "rfkill": "/usr/sbin/rfkill",
                    "iw": "/usr/sbin/iw",
                    "nmcli": "/usr/bin/nmcli",
                }.get(name)
                plan = await plan_ap_interface(
                    "wlp3s0",
                    "uap0",
                    concurrent_sta_ap=True,
                    country_code="ES",
                )
    assert plan.mode == "dedicated"
    assert plan.ap_interface == "wlp3s0"


@pytest.mark.asyncio
async def test_verify_ap_mode_ok() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "type AP\nssid nilo-node-abc"),
    ):
        with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
            assert await verify_ap_mode("uap0") is None


@pytest.mark.asyncio
async def test_verify_ap_mode_fails_when_managed() -> None:
    with patch(
        "nilo_node.network.wifi_prepare._run_cmd",
        new_callable=AsyncMock,
        return_value=(0, "type managed\nssid HomeWiFi"),
    ):
        with patch("nilo_node.network.wifi_prepare.shutil.which", return_value="/usr/sbin/iw"):
            err = await verify_ap_mode("wlp3s0")
            assert err is not None
            assert "not in AP mode" in err
