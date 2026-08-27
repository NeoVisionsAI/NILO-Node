"""WiFi access point management (hostapd + dnsmasq) with dev mock mode."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from nilo_node.config.models import AppConfig, WifiConfig
from nilo_node.network.wifi_detect import detect_wifi_interface, resolve_wifi_interface

logger = logging.getLogger(__name__)


class WifiApStatus(BaseModel):
    enabled: bool
    running: bool = False
    mock: bool = False
    ssid: str | None = None
    interface: str = "wlan0"
    ap_ip: str = "192.168.50.1"
    error: str | None = None


class WifiApManager:
    """Starts a local AP for NILO-Cardmed-Dev. Falls back to mock when hardware is unavailable."""

    def __init__(self, config: AppConfig, storage_base: Path, node_id: str) -> None:
        self._wifi: WifiConfig = config.wifi
        self._config_dir = storage_base / "wifi"
        self._node_id = node_id
        self._hostapd_proc: asyncio.subprocess.Process | None = None
        self._dnsmasq_proc: asyncio.subprocess.Process | None = None
        self._mock = False
        self._error: str | None = None
        self._started = False
        self._active_interface: str | None = None

    def resolved_interface(self) -> str:
        if self._active_interface:
            return self._active_interface
        return resolve_wifi_interface(self._wifi.interface)

    def _resolve_and_bind_interface(self) -> str | None:
        iface = detect_wifi_interface(self._wifi.interface)
        self._active_interface = iface
        return iface

    def ssid_for_node(self) -> str:
        short_id = self._node_id.replace("-", "")[:8]
        return f"{self._wifi.ssid_prefix}-{short_id}"

    def get_status(self) -> WifiApStatus:
        running = self._started and (
            self._mock
            or (
                self._hostapd_proc is not None
                and self._hostapd_proc.returncode is None
            )
        )
        return WifiApStatus(
            enabled=self._wifi.enabled,
            running=running,
            mock=self._mock,
            ssid=self.ssid_for_node() if self._wifi.enabled else None,
            interface=self.resolved_interface() if self._wifi.enabled else self._wifi.interface,
            ap_ip=self._wifi.ap_ip,
            error=self._error,
        )

    async def start(self) -> None:
        if not self._wifi.enabled or self._started:
            return

        self._config_dir.mkdir(parents=True, exist_ok=True)
        ssid = self.ssid_for_node()

        iface = self._resolve_and_bind_interface()
        if iface is None:
            self._error = "No WiFi interface found on host"
            if self._wifi.mock_when_unavailable:
                self._mock = True
                self._started = True
                logger.warning("WiFi AP mock mode — no wireless interface detected")
                return
            raise RuntimeError(self._error)

        self._write_hostapd_config(ssid)
        self._write_dnsmasq_config()

        if self._wifi.mock_when_unavailable and not self._interface_available():
            self._mock = True
            self._started = True
            logger.info(
                "WiFi AP mock mode (interface %s unavailable): ssid=%s",
                iface,
                ssid,
            )
            return

        try:
            await self._configure_interface()
            await self._start_processes()
            self._started = True
            logger.info("WiFi AP started: ssid=%s interface=%s", ssid, iface)
        except Exception as exc:
            self._error = str(exc)
            if self._wifi.mock_when_unavailable:
                self._mock = True
                self._started = True
                logger.warning("WiFi AP falling back to mock mode: %s", exc)
            else:
                logger.error("WiFi AP failed to start: %s", exc)
                raise

    async def stop(self) -> None:
        if not self._started:
            return
        for proc, name in (
            (self._dnsmasq_proc, "dnsmasq"),
            (self._hostapd_proc, "hostapd"),
        ):
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                logger.debug("Stopped %s", name)
        self._hostapd_proc = None
        self._dnsmasq_proc = None
        self._started = False
        self._mock = False
        self._active_interface = None

    async def restart(self) -> WifiApStatus:
        await self.stop()
        self._error = None
        await self.start()
        return self.get_status()

    def _netmask_prefix(self) -> int:
        parts = self._wifi.netmask.split(".")
        if len(parts) != 4:
            return 24
        binary = "".join(f"{int(p):08b}" for p in parts)
        return binary.count("1")

    async def _configure_interface(self) -> None:
        if not self._wifi.configure_interface_ip:
            return
        ip_cmd = shutil.which("ip")
        if not ip_cmd:
            raise RuntimeError(
                "ip command not found (install iproute2 in container or on host)"
            )
        iface = self._active_interface or self.resolved_interface()
        prefix = self._netmask_prefix()
        cidr = f"{self._wifi.ap_ip}/{prefix}"
        commands = [
            [ip_cmd, "link", "set", iface, "up"],
            [ip_cmd, "addr", "flush", "dev", iface, "label", iface],
            [ip_cmd, "addr", "add", cidr, "dev", iface],
        ]
        for cmd in commands:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 and cmd[1] != "addr":
                raise RuntimeError(stderr.decode() or f"Failed: {' '.join(cmd)}")
            if proc.returncode != 0 and cmd[1] == "addr" and cmd[2] == "flush":
                logger.debug("Interface flush skipped: %s", stderr.decode().strip())

    def _interface_available(self) -> bool:
        iface = self._active_interface or self.resolved_interface()
        return Path(f"/sys/class/net/{iface}").exists()

    def _write_hostapd_config(self, ssid: str) -> None:
        path = self._config_dir / "hostapd.conf"
        iface = self._active_interface or self.resolved_interface()
        lines = [
            f"interface={iface}",
            "driver=nl80211",
            f"ssid={ssid}",
            f"channel={self._wifi.channel}",
            f"country_code={self._wifi.country_code}",
            "hw_mode=g",
            "ieee80211n=1",
            "wmm_enabled=1",
        ]
        if self._wifi.password:
            lines.extend(
                [
                    "wpa=2",
                    f"wpa_passphrase={self._wifi.password}",
                    "wpa_key_mgmt=WPA-PSK",
                    "rsn_pairwise=CCMP",
                ]
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_dnsmasq_config(self) -> None:
        path = self._config_dir / "dnsmasq.conf"
        iface = self._active_interface or self.resolved_interface()
        content = f"""interface={iface}
bind-interfaces
dhcp-range={self._wifi.dhcp_range_start},{self._wifi.dhcp_range_end},{self._wifi.netmask},12h
dhcp-option=3,{self._wifi.ap_ip}
dhcp-option=6,{self._wifi.ap_ip}
address=/{self.ssid_for_node().lower()}.local/{self._wifi.ap_ip}
"""
        path.write_text(content, encoding="utf-8")

    async def _start_processes(self) -> None:
        hostapd_conf = self._config_dir / "hostapd.conf"
        dnsmasq_conf = self._config_dir / "dnsmasq.conf"

        if not self._binary_available("hostapd"):
            raise RuntimeError("hostapd binary not found")
        if not self._binary_available("dnsmasq"):
            raise RuntimeError("dnsmasq binary not found")

        self._hostapd_proc = await asyncio.create_subprocess_exec(
            "hostapd",
            str(hostapd_conf),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.5)
        if self._hostapd_proc.returncode is not None:
            stderr = await self._hostapd_proc.stderr.read() if self._hostapd_proc.stderr else b""
            raise RuntimeError(stderr.decode() or "hostapd exited immediately")

        self._dnsmasq_proc = await asyncio.create_subprocess_exec(
            "dnsmasq",
            "--conf-file",
            str(dnsmasq_conf),
            "--keep-in-foreground",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.sleep(0.2)
        if self._dnsmasq_proc.returncode is not None:
            stderr = await self._dnsmasq_proc.stderr.read() if self._dnsmasq_proc.stderr else b""
            raise RuntimeError(stderr.decode() or "dnsmasq exited immediately")

    @staticmethod
    def _binary_available(name: str) -> bool:
        try:
            subprocess.run(
                ["which", name],
                check=True,
                capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False
