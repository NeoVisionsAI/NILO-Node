"""Bluetooth device discovery via BlueZ (bluetoothctl) with mock fallback."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Iterable

from nilo_node.bluetooth.models import BluetoothDeviceInfo, normalize_mac

logger = logging.getLogger(__name__)

_DEVICE_LINE = re.compile(
    r"^Device\s+(?P<mac>[0-9A-Fa-f:]{17})\s+(?P<name>.+)$"
)
_RSSI_LINE = re.compile(r"RSSI:\s*(?P<rssi>-?\d+)")


def bluetoothctl_available() -> bool:
    return shutil.which("bluetoothctl") is not None


def mock_devices() -> list[BluetoothDeviceInfo]:
    return [
        BluetoothDeviceInfo(
            mac_address="AA:BB:CC:DD:EE:01",
            name="Mock BT Mic 1",
            rssi=-55,
            paired=False,
            connected=False,
        ),
        BluetoothDeviceInfo(
            mac_address="AA:BB:CC:DD:EE:02",
            name="Mock BT Mic 2",
            rssi=-62,
            paired=True,
            connected=False,
        ),
    ]


async def _run_bluetoothctl(*args: str, timeout: float = 30.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"bluetoothctl timed out: {' '.join(args)}")
    output = stdout.decode("utf-8", errors="replace")
    if proc.returncode not in (0, None):
        logger.debug("bluetoothctl %s → rc=%s\n%s", args, proc.returncode, output)
    return output


def _parse_devices_output(output: str) -> list[BluetoothDeviceInfo]:
    devices: dict[str, BluetoothDeviceInfo] = {}
    current_mac: str | None = None
    for line in output.splitlines():
        line = line.strip()
        match = _DEVICE_LINE.match(line)
        if match:
            mac = normalize_mac(match.group("mac"))
            name = match.group("name").strip()
            devices[mac] = BluetoothDeviceInfo(
                mac_address=mac,
                name=name,
                paired=False,
                connected=False,
            )
            current_mac = mac
            continue
        if current_mac and (rssi_match := _RSSI_LINE.search(line)):
            devices[current_mac].rssi = int(rssi_match.group("rssi"))
    return list(devices.values())


async def list_known_devices() -> list[BluetoothDeviceInfo]:
    output = await _run_bluetoothctl("devices")
    return _parse_devices_output(output)


async def scan_devices(timeout_sec: int) -> list[BluetoothDeviceInfo]:
    await power_on_adapter()
    await _run_bluetoothctl("scan", "on", timeout=2.0)
    try:
        await asyncio.sleep(timeout_sec)
    finally:
        await _run_bluetoothctl("scan", "off", timeout=5.0)
    scanned = await list_known_devices()
    paired = await list_devices_by_filter("Paired")
    connected = await list_devices_by_filter("Connected")
    devices = merge_device_lists(scanned, paired, connected)
    paired_macs = {d.mac_address for d in paired}
    connected_macs = {d.mac_address for d in connected}
    for device in devices:
        device.paired = device.paired or device.mac_address in paired_macs
        device.connected = device.connected or device.mac_address in connected_macs
    return devices


async def list_devices_by_filter(filter_name: str) -> list[BluetoothDeviceInfo]:
    output = await _run_bluetoothctl("devices", filter_name)
    return _parse_devices_output(output)


async def connect_device(mac_address: str) -> None:
    mac = normalize_mac(mac_address)
    output = await _run_bluetoothctl("connect", mac, timeout=20.0)
    if "Failed to connect" in output or "not available" in output.lower():
        await _run_bluetoothctl("pair", mac, timeout=30.0)
        await _run_bluetoothctl("trust", mac, timeout=10.0)
        output = await _run_bluetoothctl("connect", mac, timeout=20.0)
    if "Failed to connect" in output or "not available" in output.lower():
        raise RuntimeError(output.strip() or f"Failed to connect to {mac}")


async def remove_device(mac_address: str) -> None:
    mac = normalize_mac(mac_address)
    try:
        await _run_bluetoothctl("disconnect", mac, timeout=10.0)
    except RuntimeError:
        pass
    output = await _run_bluetoothctl("remove", mac, timeout=15.0)
    if "Failed to remove" in output and "not available" not in output.lower():
        raise RuntimeError(output.strip() or f"Failed to remove {mac}")


async def sync_connected_macs() -> set[str]:
    connected = await list_devices_by_filter("Connected")
    return {device.mac_address for device in connected}


async def disconnect_device(mac_address: str) -> None:
    mac = normalize_mac(mac_address)
    output = await _run_bluetoothctl("disconnect", mac, timeout=10.0)
    if "Failed to disconnect" in output:
        raise RuntimeError(output.strip() or f"Failed to disconnect {mac}")


async def power_on_adapter() -> None:
    await _run_bluetoothctl("power", "on", timeout=5.0)


async def adapter_powered() -> bool:
    output = await _run_bluetoothctl("show", timeout=5.0)
    return "Powered: yes" in output


def merge_device_lists(*lists: Iterable[BluetoothDeviceInfo]) -> list[BluetoothDeviceInfo]:
    merged: dict[str, BluetoothDeviceInfo] = {}
    for devices in lists:
        for device in devices:
            existing = merged.get(device.mac_address)
            if existing is None:
                merged[device.mac_address] = device
            else:
                merged[device.mac_address] = existing.model_copy(
                    update={
                        "name": device.name or existing.name,
                        "rssi": device.rssi if device.rssi is not None else existing.rssi,
                        "paired": existing.paired or device.paired,
                        "connected": existing.connected or device.connected,
                    }
                )
    return sorted(merged.values(), key=lambda d: d.name or d.mac_address)
