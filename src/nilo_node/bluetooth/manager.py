"""Bluetooth mic connection, per-device recording, and chunk capture."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from nilo_node.bluetooth import discovery
from nilo_node.bluetooth.models import (
    BluetoothAdapterState,
    BluetoothDeviceInfo,
    BluetoothMicRecord,
    BluetoothStatus,
    mac_file_id,
    normalize_mac,
)
from nilo_node.bluetooth.writers import AudioTrackWriter, create_audio_writer
from nilo_node.config.models import AppConfig, BluetoothConfig
from nilo_node.monitoring.models import Campaign
from nilo_node.sources.base import SourceManifest
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)


@dataclass
class AudioChunkSession:
    chunk_id: str
    chunk_path: Path
    writers: dict[str, AudioTrackWriter] = field(default_factory=dict)
    task: asyncio.Task | None = None
    running: bool = False


class BluetoothManager:
    """Discover, connect, and record from Bluetooth microphones."""

    def __init__(self, config: AppConfig, repo: StateRepository) -> None:
        self._config = config
        self._bt: BluetoothConfig = config.bluetooth
        self._repo = repo
        self._lock = asyncio.Lock()
        self._mock = False
        self._powered = False
        self._error: str | None = None
        self._adapter_state = BluetoothAdapterState.STOPPED
        self._capture_enabled = True
        self._active_campaign: Campaign | None = None
        self._sessions: dict[str, AudioChunkSession] = {}
        self._session_manifests: dict[str, dict] = {}

    def _adapter_available(self) -> bool:
        return Path(f"/sys/class/bluetooth/{self._bt.adapter}").exists()

    def set_campaign(self, campaign: Campaign | None) -> None:
        self._active_campaign = campaign
        if campaign is None:
            self._capture_enabled = True
            return
        audio_source = campaign.sources.get("audio")
        if audio_source is not None:
            self._capture_enabled = audio_source.enabled
        else:
            self._capture_enabled = True
        logger.info("Audio capture enabled from campaign: %s", self._capture_enabled)

    async def start(self) -> None:
        if not self._bt.enabled:
            self._adapter_state = BluetoothAdapterState.STOPPED
            return

        self._adapter_state = BluetoothAdapterState.STARTING
        if self._bt.mock_when_unavailable and (
            not discovery.bluetoothctl_available() or not self._adapter_available()
        ):
            self._mock = True
            self._powered = True
            self._adapter_state = BluetoothAdapterState.MOCK
            logger.info(
                "Bluetooth mock mode (adapter %s unavailable or bluetoothctl missing)",
                self._bt.adapter,
            )
            return

        try:
            if self._bt.auto_power_on:
                await discovery.power_on_adapter()
            self._powered = await discovery.adapter_powered()
            self._adapter_state = BluetoothAdapterState.RUNNING
            logger.info("Bluetooth adapter ready: %s", self._bt.adapter)
        except Exception as exc:
            self._error = str(exc)
            if self._bt.mock_when_unavailable:
                self._mock = True
                self._powered = True
                self._adapter_state = BluetoothAdapterState.MOCK
                logger.warning("Bluetooth falling back to mock mode: %s", exc)
            else:
                self._adapter_state = BluetoothAdapterState.ERROR
                logger.error("Bluetooth failed to start: %s", exc)
                raise

    async def stop(self) -> None:
        for chunk_id in list(self._sessions.keys()):
            await self.abort_chunk(chunk_id)
        self._adapter_state = BluetoothAdapterState.STOPPED
        self._powered = False

    async def discover(self) -> list[BluetoothDeviceInfo]:
        async with self._lock:
            if self._mock:
                return discovery.mock_devices()
            devices = await discovery.scan_devices(self._bt.scan_timeout_sec)
            for device in devices:
                self._repo.touch_bluetooth_mic_seen(device.mac_address, device.name)
            return devices

    async def connect(self, mac_address: str, device_name: str | None = None) -> BluetoothMicRecord:
        mac = normalize_mac(mac_address)
        async with self._lock:
            if self._mock:
                name = device_name or f"Mock Mic {mac_file_id(mac)[-2:]}"
                record = self._repo.upsert_bluetooth_mic(
                    mac,
                    device_name=name,
                    connected=True,
                    record_enabled=self._bt.default_record_on_connect,
                    paired=True,
                )
                self._repo.upsert_device(
                    mac,
                    "bluetooth_mic",
                    "connected",
                    {"device_name": name, "record_enabled": record.record_enabled},
                )
                self._repo.insert_device_event(mac, "connected", {"mock": True})
                return record

            await discovery.connect_device(mac)
            known = await discovery.list_known_devices()
            matched = next((d for d in known if d.mac_address == mac), None)
            name = device_name or (matched.name if matched else None)
            record = self._repo.upsert_bluetooth_mic(
                mac,
                device_name=name,
                connected=True,
                record_enabled=self._bt.default_record_on_connect,
                paired=True,
            )
            self._repo.upsert_device(
                mac,
                "bluetooth_mic",
                "connected",
                {"device_name": name, "record_enabled": record.record_enabled},
            )
            self._repo.insert_device_event(mac, "connected", {})
            return record

    async def disconnect(self, mac_address: str) -> BluetoothMicRecord:
        mac = normalize_mac(mac_address)
        async with self._lock:
            existing = self._repo.get_bluetooth_mic(mac)
            if existing is None:
                raise LookupError(f"Mic not known: {mac}")

            if not self._mock:
                await discovery.disconnect_device(mac)

            record = self._repo.upsert_bluetooth_mic(
                mac,
                device_name=existing.device_name,
                connected=False,
                record_enabled=existing.record_enabled,
                paired=existing.paired,
            )
            self._repo.upsert_device(
                mac,
                "bluetooth_mic",
                "disconnected",
                {
                    "device_name": existing.device_name,
                    "record_enabled": existing.record_enabled,
                },
            )
            self._repo.insert_device_event(mac, "disconnected", {})
            return record

    def set_recording(self, mac_address: str, record_enabled: bool) -> BluetoothMicRecord:
        mac = normalize_mac(mac_address)
        record = self._repo.set_bluetooth_mic_recording(mac, record_enabled)
        if record is None:
            raise LookupError(f"Mic not known: {mac}. Connect first.")
        self._repo.upsert_device(
            mac,
            "bluetooth_mic",
            "connected" if record.connected else "disconnected",
            {
                "device_name": record.device_name,
                "record_enabled": record.record_enabled,
            },
        )
        self._repo.insert_device_event(
            mac,
            "recording_enabled" if record_enabled else "recording_disabled",
            {"record_enabled": record_enabled},
        )
        logger.info("Mic %s record_enabled=%s", mac, record_enabled)
        return record

    def get_status(self) -> BluetoothStatus:
        mics = self._repo.list_bluetooth_mics()
        connected = [m for m in mics if m.connected]
        recording = [m for m in connected if m.record_enabled and self._capture_enabled]
        return BluetoothStatus(
            enabled=self._bt.enabled,
            adapter=self._bt.adapter,
            adapter_state=self._adapter_state,
            mock=self._mock,
            powered=self._powered,
            discoverable=False,
            connected_count=len(connected),
            recording_count=len(recording),
            capture_enabled=self._capture_enabled,
            mics=mics,
            error=self._error,
        )

    def list_mics(self) -> list[BluetoothMicRecord]:
        return self._repo.list_bluetooth_mics()

    async def begin_chunk(self, chunk_id: str, chunk_path: Path) -> None:
        if not self._capture_enabled:
            return

        recording_mics = [
            mic
            for mic in self._repo.list_bluetooth_mics()
            if mic.connected and mic.record_enabled
        ]
        if not recording_mics:
            return

        audio_dir = chunk_path / "sources" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        mode = "mock" if self._mock else "mock"
        session = AudioChunkSession(chunk_id=chunk_id, chunk_path=chunk_path)
        for mic in recording_mics:
            session.writers[mic.mac_address] = create_audio_writer(
                mode,
                audio_dir,
                mic.mac_address,
                self._bt.sample_rate,
                self._bt.channels,
            )
        session.running = True
        session.task = asyncio.create_task(self._capture_loop(session))
        self._sessions[chunk_id] = session

    async def _capture_loop(self, session: AudioChunkSession) -> None:
        interval = 0.1
        try:
            while session.running:
                ts = time.time()
                for writer in session.writers.values():
                    writer.write_samples(ts)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def finalize_chunk(self, chunk_id: str) -> SourceManifest | None:
        session = self._sessions.pop(chunk_id, None)
        if session is None:
            mics = self._repo.list_bluetooth_mics()
            if not any(m.connected and m.record_enabled for m in mics):
                return SourceManifest(
                    path="sources/audio/",
                    stub=True,
                    extra={"skipped": True, "reason": "no recording mics"},
                )
            return SourceManifest(
                path="sources/audio/",
                stub=True,
                extra={"skipped": True, "reason": "capture disabled or no active session"},
            )

        session.running = False
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass

        tracks = [writer.finalize() for writer in session.writers.values()]
        manifest = {"tracks": tracks}
        self._session_manifests[chunk_id] = manifest
        return SourceManifest(path="sources/audio/", extra=manifest)

    async def abort_chunk(self, chunk_id: str) -> None:
        session = self._sessions.pop(chunk_id, None)
        if session is None:
            return
        session.running = False
        if session.task is not None:
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
