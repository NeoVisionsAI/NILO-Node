"""WiFi access point management (hostapd + dnsmasq) with dev mock mode."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel

from nilo_node.config.models import AppConfig, WifiConfig
from nilo_node.network.wifi_detect import detect_wifi_interface, resolve_wifi_interface
from nilo_node.network.wifi_host import resolve_wifi_backend, run_host_wifi_script
from nilo_node.network.wifi_prepare import plan_ap_interface, verify_ap_mode

logger = logging.getLogger(__name__)


def _ip_addr_error_is_benign(message: str) -> bool:
    """Ignore idempotent RTNETLINK errors when re-applying AP network config."""
    lowered = message.lower()
    return any(
        token in lowered
        for token in (
            "file exists",
            "name not unique",
            "already exists",
        )
    )


class WifiApStatus(BaseModel):
    enabled: bool
    running: bool = False
    mock: bool = False
    ssid: str | None = None
    interface: str = "wlan0"
    sta_interface: str | None = None
    ap_interface: str | None = None
    ap_mode: str | None = None  # concurrent | dedicated | mock
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
        self._sta_interface: str | None = None
        self._ap_mode: str | None = None
        self._backend: str = "container"

    def resolved_interface(self) -> str:
        if self._active_interface:
            return self._active_interface
        return resolve_wifi_interface(self._wifi.interface)

    def _detect_sta_interface(self) -> str | None:
        return detect_wifi_interface(self._wifi.interface)

    def ssid_for_node(self) -> str:
        short_id = self._node_id.replace("-", "")[:8]
        return f"{self._wifi.ssid_prefix}-{short_id}"

    def get_status(self) -> WifiApStatus:
        running = self._started and (
            self._mock
            or self._backend == "host"
            or (
                self._hostapd_proc is not None
                and self._hostapd_proc.returncode is None
            )
        )
        iface = self._active_interface or self.resolved_interface()
        return WifiApStatus(
            enabled=self._wifi.enabled,
            running=running,
            mock=self._mock,
            ssid=self.ssid_for_node() if self._wifi.enabled else None,
            interface=iface if self._wifi.enabled else self._wifi.interface,
            sta_interface=self._sta_interface,
            ap_interface=self._active_interface,
            ap_mode=self._ap_mode,
            ap_ip=self._wifi.ap_ip,
            error=self._error,
        )

    async def start(self) -> None:
        if not self._wifi.enabled or self._started:
            return

        self._config_dir.mkdir(parents=True, exist_ok=True)
        ssid = self.ssid_for_node()

        iface = self._detect_sta_interface()
        if iface is None:
            self._error = "No WiFi interface found on host"
            if self._wifi.mock_when_unavailable:
                self._mock = True
                self._started = True
                logger.warning("WiFi AP mock mode — no wireless interface detected")
                return
            raise RuntimeError(self._error)

        if not self._wifi.hardware_ap:
            self._mock = True
            self._started = True
            self._ap_mode = "mock"
            self._sta_interface = iface
            logger.warning(
                "WiFi AP mock mode — wifi.hardware_ap=false (dev safety; enable on mini PC only)"
            )
            return

        self._backend = resolve_wifi_backend(
            self._wifi.backend,
            self._wifi.host_script_path,
        )
        if self._backend == "host":
            await self._start_via_host_script()
            return

        if self._wifi.mock_when_unavailable and not self._interface_available(iface):
            self._mock = True
            self._started = True
            logger.info(
                "WiFi AP mock mode (interface %s unavailable): ssid=%s",
                iface,
                ssid,
            )
            return

        try:
            plan = await plan_ap_interface(
                iface,
                self._wifi.ap_interface,
                concurrent_sta_ap=self._wifi.concurrent_sta_ap,
                country_code=self._wifi.country_code,
            )
            self._sta_interface = plan.sta_interface
            self._active_interface = plan.ap_interface
            self._ap_mode = plan.mode
            self._write_hostapd_config(ssid)
            self._write_dnsmasq_config()
            await self._configure_interface()
            await self._start_processes()
            ap_error = await verify_ap_mode(plan.ap_interface)
            if ap_error:
                raise RuntimeError(ap_error)
            self._started = True
            logger.info(
                "WiFi AP started: ssid=%s mode=%s sta=%s ap=%s",
                ssid,
                plan.mode,
                plan.sta_interface,
                plan.ap_interface,
            )
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
        if self._backend == "host":
            await self._stop_via_host_script()
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
        self._sta_interface = None
        self._ap_mode = None
        self._backend = "container"

    async def _start_via_host_script(self) -> None:
        script = self._wifi.host_script_path
        try:
            code, output = await run_host_wifi_script(script, "start")
            if code != 0:
                raise RuntimeError(output or f"Host WiFi script failed ({code})")
            self._started = True
            self._mock = False
            self._ap_mode = "host"
            self._sta_interface = self._detect_sta_interface()
            self._active_interface = self._wifi.ap_interface or self._sta_interface
            logger.info("WiFi AP started via host script: %s", script)
        except Exception as exc:
            self._error = str(exc)
            if self._wifi.mock_when_unavailable:
                self._mock = True
                self._started = True
                logger.warning("WiFi AP host script failed — mock mode: %s", exc)
            else:
                raise

    async def _stop_via_host_script(self) -> None:
        script = self._wifi.host_script_path
        if Path(script).is_file():
            await run_host_wifi_script(script, "stop")

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
        commands: list[tuple[list[str], bool]] = [
            ([ip_cmd, "link", "set", iface, "up"], True),
            ([ip_cmd, "addr", "flush", "dev", iface], False),
            ([ip_cmd, "addr", "replace", cidr, "dev", iface], False),
        ]
        for cmd, required in commands:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                if not required and _ip_addr_error_is_benign(err):
                    logger.debug("Interface setup note (%s): %s", " ".join(cmd[2:]), err)
                    continue
                if required:
                    raise RuntimeError(err or f"Failed: {' '.join(cmd)}")
                logger.warning("Interface setup skipped (%s): %s", " ".join(cmd[2:]), err)

    def _interface_available(self, iface: str | None = None) -> bool:
        name = iface or self._sta_interface or resolve_wifi_interface(self._wifi.interface)
        return Path(f"/sys/class/net/{name}").exists()

    def _write_hostapd_config(self, ssid: str) -> None:
        path = self._config_dir / "hostapd.conf"
        iface = self._active_interface or self.resolved_interface()
        lines = [
            f"interface={iface}",
            "driver=nl80211",
            f"ssid={ssid}",
            f"channel={self._wifi.channel}",
            f"country_code={self._wifi.country_code}",
            "ieee80211d=1",
            "hw_mode=g",
            "ieee80211n=1",
            "wmm_enabled=1",
            "auth_algs=1",
            "ignore_broadcast_ssid=0",
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
        # Self-contained AP DHCP only (port=0 disables DNS). No netmask in dhcp-range.
        content = f"""# NILO-Node WiFi AP
interface={iface}
bind-interfaces
except-interface=lo
port=0
no-resolv
no-hosts
dhcp-authoritative
dhcp-range={self._wifi.dhcp_range_start},{self._wifi.dhcp_range_end},12h
dhcp-option=option:router,{self._wifi.ap_ip}
dhcp-option=option:dns-server,{self._wifi.ap_ip}
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

        dnsmasq_bin = Path("/usr/sbin/dnsmasq")
        if not dnsmasq_bin.is_file():
            dnsmasq_bin = Path(shutil.which("dnsmasq") or "dnsmasq")
        conf_path = str(dnsmasq_conf.resolve())
        test_proc = await asyncio.create_subprocess_exec(
            str(dnsmasq_bin),
            f"--conf-file={conf_path}",
            "--test",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, test_stderr = await test_proc.communicate()
        if test_proc.returncode != 0:
            raise RuntimeError(test_stderr.decode() or "dnsmasq config test failed")

        self._dnsmasq_proc = await asyncio.create_subprocess_exec(
            str(dnsmasq_bin),
            f"--conf-file={conf_path}",
            "-k",
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
