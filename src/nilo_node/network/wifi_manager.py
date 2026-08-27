"""WiFi access point management (hostapd + dnsmasq) with dev mock mode."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

from pydantic import BaseModel

from nilo_node.config.models import AppConfig, WifiConfig
from nilo_node.network.wifi_detect import detect_wifi_interface, resolve_wifi_interface
from nilo_node.network.wifi_host import (
    host_ap_running,
    resolve_wifi_backend,
    run_host_wifi_script,
)
from nilo_node.network.wifi_prepare import (
    ApInterfacePlan,
    configure_ap_interface_ip,
    detect_operating_channel,
    ensure_concurrent_ap_ready,
    hw_mode_for_channel,
    plan_ap_interface,
    pkill_pattern,
    teardown_virtual_ap,
    verify_ap_mode,
)

logger = logging.getLogger(__name__)


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
        self._hostapd_pid: int | None = None
        self._dnsmasq_proc: asyncio.subprocess.Process | None = None
        self._mock = False
        self._error: str | None = None
        self._started = False
        self._active_interface: str | None = None
        self._sta_interface: str | None = None
        self._ap_mode: str | None = None
        self._backend: str = "container"
        self._hostapd_channel: int | None = None
        self._hostapd_hw_mode: str = "g"
        self._lifecycle_lock = asyncio.Lock()

    def resolved_interface(self) -> str:
        if self._active_interface:
            return self._active_interface
        return resolve_wifi_interface(self._wifi.interface)

    def _detect_sta_interface(self) -> str | None:
        exclude: frozenset[str] = frozenset()
        ap_name = (self._wifi.ap_interface or "").strip()
        if ap_name:
            exclude = frozenset({ap_name})
        return detect_wifi_interface(self._wifi.interface, exclude=exclude)

    def ssid_for_node(self) -> str:
        short_id = self._node_id.replace("-", "")[:8]
        return f"{self._wifi.ssid_prefix}-{short_id}"

    def get_status(self) -> WifiApStatus:
        if self._backend == "host" and self._started and not self._mock:
            running = host_ap_running() and not self._error
        else:
            running = self._started and not self._error and (
                self._mock
                or self._backend == "host"
                or self._hostapd_is_running()
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
        async with self._lifecycle_lock:
            await self._start_unlocked()

    async def _start_unlocked(self) -> None:
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
            await self._attempt_start_ap(iface, ssid, concurrent=self._wifi.concurrent_sta_ap)
            self._started = True
            self._error = None
            logger.info(
                "WiFi AP started: ssid=%s mode=%s sta=%s ap=%s",
                ssid,
                self._ap_mode,
                self._sta_interface,
                self._active_interface,
            )
        except Exception as exc:
            await self._kill_ap_processes()
            if (
                self._active_interface
                and self._sta_interface
                and self._active_interface != self._sta_interface
            ):
                await teardown_virtual_ap(self._active_interface)
            self._active_interface = None
            self._sta_interface = None
            self._ap_mode = None
            self._error = str(exc)
            if self._wifi.mock_when_unavailable:
                self._mock = True
                self._started = True
                logger.warning("WiFi AP falling back to mock mode: %s", exc)
            else:
                logger.error("WiFi AP failed to start: %s", exc)
                raise

    async def _attempt_start_ap(self, sta_iface: str, ssid: str, *, concurrent: bool) -> None:
        plan = await plan_ap_interface(
            sta_iface,
            self._wifi.ap_interface,
            concurrent_sta_ap=concurrent,
            country_code=self._wifi.country_code,
            ap_ip=self._wifi.ap_ip,
            netmask_prefix=self._netmask_prefix(),
            default_channel=self._wifi.channel,
        )
        try:
            await self._apply_ap_plan(ssid, plan)
        except Exception as exc:
            if concurrent and plan.mode == "concurrent":
                logger.warning(
                    "AP+STA concurrente falló (%s) — reintentando tras reset de %s",
                    exc,
                    self._wifi.ap_interface,
                )
                await self._kill_ap_processes()
                if self._wifi.ap_interface:
                    await teardown_virtual_ap(self._wifi.ap_interface)
                await asyncio.sleep(1.0)
                plan = await plan_ap_interface(
                    sta_iface,
                    self._wifi.ap_interface,
                    concurrent_sta_ap=True,
                    country_code=self._wifi.country_code,
                    ap_ip=self._wifi.ap_ip,
                    netmask_prefix=self._netmask_prefix(),
                    default_channel=self._wifi.channel,
                )
                await self._apply_ap_plan(ssid, plan)
            else:
                raise

    async def _apply_ap_plan(self, ssid: str, plan: ApInterfacePlan) -> None:
        self._sta_interface = plan.sta_interface
        self._active_interface = plan.ap_interface
        self._ap_mode = plan.mode
        self._hostapd_channel = plan.channel
        self._hostapd_hw_mode = plan.hw_mode
        await self._kill_ap_processes()
        hostapd_conf = str(self._config_dir / "hostapd.conf")
        if (
            plan.mode == "concurrent"
            and plan.ap_interface != plan.sta_interface
        ):
            actual_ap = await ensure_concurrent_ap_ready(
                plan.sta_interface,
                plan.ap_interface,
                ap_ip=self._wifi.ap_ip,
                netmask_prefix=self._netmask_prefix(),
                hostapd_conf=hostapd_conf,
            )
            self._active_interface = actual_ap
        self._write_hostapd_config(ssid)
        self._write_dnsmasq_config()
        try:
            await self._start_hostapd()
        except RuntimeError as exc:
            await self._capture_hostapd_debug(hostapd_conf)
            raise RuntimeError(
                f"{exc}\n(hostapd debug: {self._config_dir / 'hostapd-debug.log'})"
            ) from exc
        ap_iface = self._active_interface or plan.ap_interface
        if plan.mode == "concurrent" and ap_iface != plan.sta_interface:
            await configure_ap_interface_ip(
                ap_iface, self._wifi.ap_ip, self._netmask_prefix()
            )
        if not self._binary_available("dnsmasq"):
            raise RuntimeError("dnsmasq binary not found")
        await self._start_dnsmasq()
        await asyncio.sleep(1.0)
        ap_error = await verify_ap_mode(self._active_interface or plan.ap_interface)
        if ap_error:
            raise RuntimeError(ap_error)

    def _hostapd_is_running(self) -> bool:
        if self._hostapd_proc is not None and self._hostapd_proc.returncode is None:
            return True
        if self._hostapd_pid is not None:
            try:
                os.kill(self._hostapd_pid, 0)
                return True
            except ProcessLookupError:
                return False
        pid_file = self._config_dir / "hostapd.pid"
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text().strip()), 0)
                return True
            except (ProcessLookupError, ValueError):
                return False
        return False

    async def _kill_hostapd_daemon(self) -> None:
        hostapd_conf = self._config_dir / "hostapd.conf"
        pid_file = self._config_dir / "hostapd.pid"
        pid_candidates: list[int] = []
        if self._hostapd_pid is not None:
            pid_candidates.append(self._hostapd_pid)
        if pid_file.is_file():
            try:
                pid_candidates.append(int(pid_file.read_text().strip()))
            except ValueError:
                pass
        for pid in dict.fromkeys(pid_candidates):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        await asyncio.sleep(0.3)
        if hostapd_conf.is_file():
            await pkill_pattern(str(hostapd_conf))
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        self._hostapd_pid = None

    async def _kill_ap_processes(self) -> None:
        await self._kill_hostapd_daemon()
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

        dnsmasq_conf = self._config_dir / "dnsmasq.conf"
        if dnsmasq_conf.is_file():
            await pkill_pattern(str(dnsmasq_conf))

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_unlocked()

    async def _stop_unlocked(self) -> None:
        ap_iface = self._active_interface
        sta_iface = self._sta_interface
        ap_mode = self._ap_mode
        if self._started and self._backend == "host":
            await self._stop_via_host_script()
        await self._kill_ap_processes()
        if (
            ap_iface
            and sta_iface
            and ap_iface != sta_iface
            and ap_mode in ("concurrent", None)
        ):
            await teardown_virtual_ap(ap_iface)
        self._started = False
        self._mock = False
        self._error = None
        self._active_interface = None
        self._sta_interface = None
        self._ap_mode = None
        self._hostapd_channel = None
        self._hostapd_hw_mode = "g"
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
        try:
            await run_host_wifi_script(self._wifi.host_script_path, "stop")
        except OSError as exc:
            logger.warning("Host WiFi stop script failed: %s", exc)

    async def restart(self) -> WifiApStatus:
        async with self._lifecycle_lock:
            await self._stop_unlocked()
            self._error = None
            await self._start_unlocked()
            return self.get_status()

    def _netmask_prefix(self) -> int:
        parts = self._wifi.netmask.split(".")
        if len(parts) != 4:
            return 24
        binary = "".join(f"{int(p):08b}" for p in parts)
        return binary.count("1")

    def _interface_available(self, iface: str | None = None) -> bool:
        name = iface or self._sta_interface or resolve_wifi_interface(self._wifi.interface)
        return Path(f"/sys/class/net/{name}").exists()

    def _write_hostapd_config(self, ssid: str) -> None:
        path = self._config_dir / "hostapd.conf"
        iface = self._active_interface or self.resolved_interface()
        channel = self._hostapd_channel or self._wifi.channel
        hw_mode = self._hostapd_hw_mode
        if self._sta_interface:
            detected = detect_operating_channel(self._sta_interface)
            if detected is not None:
                channel = detected
                hw_mode = hw_mode_for_channel(channel)
        lines = [
            f"interface={iface}",
            "driver=nl80211",
            f"ssid={ssid}",
            f"channel={channel}",
            f"country_code={self._wifi.country_code}",
            "ieee80211d=1",
            f"hw_mode={hw_mode}",
            "ieee80211n=1",
            "wmm_enabled=1",
            "auth_algs=1",
            "ignore_broadcast_ssid=0",
        ]
        if hw_mode == "a":
            lines.append("ieee80211ac=1")
        if self._wifi.password:
            if len(self._wifi.password) < 8:
                logger.warning(
                    "WiFi password shorter than 8 characters — AP will be open (no WPA)"
                )
            else:
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

    async def _capture_hostapd_debug(self, hostapd_conf: str) -> None:
        """Run hostapd -dd once and save full trace for diagnosis."""
        debug_log = self._config_dir / "hostapd-debug.log"
        try:
            proc = await asyncio.create_subprocess_exec(
                "hostapd",
                "-dd",
                hostapd_conf,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
            except asyncio.TimeoutError:
                proc.kill()
                stdout, _ = await proc.communicate()
            debug_log.write_text(stdout.decode(errors="replace"), encoding="utf-8")
            logger.error("hostapd debug trace written to %s", debug_log)
        except OSError as exc:
            logger.warning("Could not capture hostapd debug: %s", exc)

    async def _start_hostapd(self) -> None:
        hostapd_conf = self._config_dir / "hostapd.conf"
        if not self._binary_available("hostapd"):
            raise RuntimeError("hostapd binary not found")

        hostapd_log = self._config_dir / "hostapd.log"
        pid_file = self._config_dir / "hostapd.pid"
        for path in (hostapd_log, pid_file):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        proc = await asyncio.create_subprocess_exec(
            "hostapd",
            "-B",
            "-P",
            str(pid_file),
            "-f",
            str(hostapd_log),
            str(hostapd_conf),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            detail = stdout.decode(errors="replace").strip()
            if hostapd_log.is_file():
                log_tail = hostapd_log.read_text(encoding="utf-8", errors="replace").strip()
                if log_tail:
                    detail = detail or log_tail
            raise RuntimeError(
                detail or f"hostapd -B failed (code {proc.returncode})"
            )

        await asyncio.sleep(1.5)
        if not pid_file.is_file():
            log_tail = ""
            if hostapd_log.is_file():
                log_tail = hostapd_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(log_tail or "hostapd did not start (missing pid file)")

        try:
            self._hostapd_pid = int(pid_file.read_text().strip())
            os.kill(self._hostapd_pid, 0)
        except (ProcessLookupError, ValueError) as exc:
            log_tail = ""
            if hostapd_log.is_file():
                log_tail = hostapd_log.read_text(encoding="utf-8", errors="replace").strip()
            raise RuntimeError(log_tail or "hostapd daemon not running after start") from exc

        self._hostapd_proc = None
        logger.info("hostapd daemon running (pid=%s)", self._hostapd_pid)

    async def _start_dnsmasq(self) -> None:
        dnsmasq_conf = self._config_dir / "dnsmasq.conf"
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
