"""Bluetooth device and status models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class BluetoothAdapterState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    MOCK = "mock"
    ERROR = "error"


class BluetoothDeviceInfo(BaseModel):
    mac_address: str
    name: str | None = None
    rssi: int | None = None
    paired: bool = False
    connected: bool = False
    alias: str | None = None


class BluetoothMicRecord(BaseModel):
    mac_address: str
    device_name: str | None = None
    connected: bool = False
    record_enabled: bool = True
    paired: bool = False
    registered_at: datetime
    last_seen_at: datetime


class BluetoothStatus(BaseModel):
    enabled: bool
    adapter: str
    adapter_state: BluetoothAdapterState
    mock: bool = False
    powered: bool = False
    discoverable: bool = False
    connected_count: int = 0
    recording_count: int = 0
    capture_enabled: bool = True
    mics: list[BluetoothMicRecord] = Field(default_factory=list)
    error: str | None = None


def normalize_mac(mac: str) -> str:
    cleaned = mac.strip().upper().replace("-", ":")
    parts = cleaned.split(":")
    if len(parts) == 6 and all(len(p) <= 2 for p in parts):
        return ":".join(p.zfill(2) for p in parts)
    raise ValueError(f"Invalid MAC address: {mac}")


def mac_file_id(mac: str) -> str:
    return normalize_mac(mac).replace(":", "")
