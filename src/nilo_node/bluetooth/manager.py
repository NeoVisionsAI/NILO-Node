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
    RecordingMode,
    mac_file_id,
    normalize_mac,
)
from nilo_node.bluetooth.test_recording import (
    cleanup_old_test_recordings,
    record_test_wav,
    test_recordings_dir,
    wav_waveform_peaks,
)
from nilo_node.bluetooth.writers import AudioTrackWriter, create_audio_writer
from nilo_node.config.models import AppConfig, BluetoothConfig
from nilo_node.monitoring.models import Campaign
from nilo_node.sources.base import SourceManifest
from nilo_node.state.repository import StateRepository

logger = logging.getLogger(__name__)

INTERVAL_BURST_SEC = 10


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
        self._last_discovered: list[BluetoothDeviceInfo] = []
        self._test_recording_dir = test_recordings_dir(Path(config.storage.base_path))
        self._reconnect_task: asyncio.Task[None] | None = None

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
        cleanup_old_test_recordings(self._test_recording_dir)
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
            await self.sync_connection_state()
            logger.info("Bluetooth adapter ready: %s", self._bt.adapter)
            self._reconnect_task = asyncio.create_task(self._auto_reconnect_loop())
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
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
        for chunk_id in list(self._sessions.keys()):
            await self.abort_chunk(chunk_id)
        self._adapter_state = BluetoothAdapterState.STOPPED
        self._powered = False

    async def sync_connection_state(self) -> None:
        if self._mock:
            return
        connected_macs = await discovery.sync_connected_macs()
        for mic in self._repo.list_bluetooth_mics():
            should_be_connected = mic.mac_address in connected_macs
            if mic.connected != should_be_connected:
                self._repo.upsert_bluetooth_mic(
                    mic.mac_address,
                    device_name=mic.device_name,
                    connected=should_be_connected,
                    record_enabled=mic.record_enabled,
                    paired=mic.paired,
                    metadata_patch={
                        "display_name": mic.display_name,
                        "recording_mode": mic.recording_mode.value,
                        "recording_interval_sec": mic.recording_interval_sec,
                        "recording_active": mic.recording_active,
                    },
                )

    async def _auto_reconnect_loop(self) -> None:
        while True:
            interval = max(15, int(self._bt.auto_reconnect_interval_sec))
            try:
                await asyncio.sleep(interval)
                if self._mock or self._adapter_state != BluetoothAdapterState.RUNNING:
                    continue
                await self.sync_connection_state()
                for mic in self._repo.list_bluetooth_mics():
                    if not mic.paired or mic.connected:
                        continue
                    try:
                        logger.info("Auto-reconnect Bluetooth mic %s", mic.mac_address)
                        await discovery.connect_device(mic.mac_address)
                        await self.sync_connection_state()
                    except Exception as exc:
                        logger.debug("Auto-reconnect failed for %s: %s", mic.mac_address, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Bluetooth auto-reconnect loop error: %s", exc)

    async def discover(self) -> list[BluetoothDeviceInfo]:
        async with self._lock:
            if self._mock:
                devices = discovery.mock_devices()
            else:
                devices = await discovery.scan_devices(self._bt.scan_timeout_sec)
                await self.sync_connection_state()
            for device in devices:
                self._repo.touch_bluetooth_mic_seen(device.mac_address, device.name)
            self._last_discovered = devices
            return devices

    def last_discovered_devices(self) -> list[BluetoothDeviceInfo]:
        return list(self._last_discovered)

    async def connect(self, mac_address: str, device_name: str | None = None) -> BluetoothMicRecord:
        mac = normalize_mac(mac_address)
        async with self._lock:
            existing = self._repo.get_bluetooth_mic(mac)
            record_on_connect = (
                self._bt.default_record_on_connect
                if existing is None
                else existing.record_enabled
            )
            if self._mock:
                name = device_name or f"Mock Mic {mac_file_id(mac)[-2:]}"
                record = self._repo.upsert_bluetooth_mic(
                    mac,
                    device_name=name,
                    connected=True,
                    record_enabled=record_on_connect,
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
            metadata_patch = None
            if existing is not None:
                metadata_patch = {
                    "display_name": existing.display_name,
                    "recording_mode": existing.recording_mode.value,
                    "recording_interval_sec": existing.recording_interval_sec,
                    "recording_active": existing.recording_active,
                }
            record = self._repo.upsert_bluetooth_mic(
                mac,
                device_name=name,
                connected=True,
                record_enabled=record_on_connect,
                paired=True,
                metadata_patch=metadata_patch,
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
                metadata_patch={
                    "display_name": existing.display_name,
                    "recording_mode": existing.recording_mode.value,
                    "recording_interval_sec": existing.recording_interval_sec,
                    "recording_active": False,
                },
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

    async def unpair(self, mac_address: str) -> None:
        mac = normalize_mac(mac_address)
        async with self._lock:
            existing = self._repo.get_bluetooth_mic(mac)
            if existing is None:
                raise LookupError(f"Mic not known: {mac}")

            if existing.connected and not self._mock:
                await discovery.disconnect_device(mac)
            if not self._mock:
                await discovery.remove_device(mac)

            self._repo.delete_bluetooth_mic(mac)
            self._repo.upsert_device(mac, "bluetooth_mic", "unpaired", {})
            self._repo.insert_device_event(mac, "unpaired", {})

    def set_recording(self, mac_address: str, record_enabled: bool) -> BluetoothMicRecord:
        return self.update_mic_settings(mac_address, record_enabled=record_enabled)

    def update_mic_settings(
        self,
        mac_address: str,
        *,
        display_name: str | None = None,
        recording_mode: RecordingMode | str | None = None,
        recording_interval_sec: int | None = None,
        record_enabled: bool | None = None,
        recording_active: bool | None = None,
    ) -> BluetoothMicRecord:
        mac = normalize_mac(mac_address)
        mode_value = None
        if recording_mode is not None:
            mode_value = (
                recording_mode.value
                if isinstance(recording_mode, RecordingMode)
                else str(recording_mode)
            )
        record = self._repo.update_bluetooth_mic_settings(
            mac,
            display_name=display_name,
            recording_mode=mode_value,
            recording_interval_sec=recording_interval_sec,
            record_enabled=record_enabled,
            recording_active=recording_active,
        )
        if record is None:
            raise LookupError(f"Mic not known: {mac}. Connect first.")

        if record_enabled is not None:
            self._repo.insert_device_event(
                mac,
                "recording_enabled" if record_enabled else "recording_disabled",
                {"record_enabled": record_enabled},
            )
            logger.info("Mic %s record_enabled=%s", mac, record_enabled)
        if recording_active is not None:
            self._repo.insert_device_event(
                mac,
                "recording_active_on" if recording_active else "recording_active_off",
                {"recording_active": recording_active},
            )

        self._repo.upsert_device(
            mac,
            "bluetooth_mic",
            "connected" if record.connected else "disconnected",
            {
                "device_name": record.device_name,
                "display_name": record.display_name,
                "record_enabled": record.record_enabled,
                "recording_mode": record.recording_mode.value,
            },
        )
        return record

    def _mic_eligible_for_chunk(self, mic: BluetoothMicRecord) -> bool:
        if not mic.connected or not mic.record_enabled:
            return False
        if mic.recording_mode == RecordingMode.ON_DEMAND:
            return mic.recording_active
        return True

    def _should_write_samples(self, mic: BluetoothMicRecord, timestamp: float) -> bool:
        if not self._mic_eligible_for_chunk(mic):
            return False
        if mic.recording_mode == RecordingMode.INTERVAL:
            interval = max(5, mic.recording_interval_sec)
            burst = min(INTERVAL_BURST_SEC, interval // 2 or INTERVAL_BURST_SEC)
            return (timestamp % interval) < burst
        return True

    def get_status(self) -> BluetoothStatus:
        mics = self._repo.list_bluetooth_mics()
        connected = [m for m in mics if m.connected]
        recording = [
            m
            for m in connected
            if self._mic_eligible_for_chunk(m) and self._capture_enabled
        ]
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

    async def record_test_sample(
        self,
        mac_address: str,
        *,
        duration_sec: float = 10.0,
    ) -> dict[str, object]:
        mac = normalize_mac(mac_address)
        mic = self._repo.get_bluetooth_mic(mac)
        if mic is None or not mic.connected:
            raise LookupError("Micrófono no conectado")

        cleanup_old_test_recordings(self._test_recording_dir)
        recording_id, path, mock_audio = await record_test_wav(
            self._test_recording_dir,
            mac,
            duration_sec=duration_sec,
            sample_rate=self._bt.sample_rate,
            channels=self._bt.channels,
            allow_mock=self._mock,
        )
        waveform = wav_waveform_peaks(path)
        return {
            "recording_id": recording_id,
            "mac_address": mac,
            "duration_sec": duration_sec,
            "playback_path": str(path),
            "mock_audio": mock_audio,
            "waveform": waveform,
        }

    def resolve_test_recording(self, recording_id: str) -> Path:
        safe_id = Path(recording_id).name
        if not safe_id.endswith(".wav"):
            safe_id = f"{safe_id}.wav"
        path = self._test_recording_dir / safe_id
        if not path.is_file():
            raise FileNotFoundError(f"Test recording not found: {recording_id}")
        return path

    async def begin_chunk(self, chunk_id: str, chunk_path: Path) -> None:
        if not self._capture_enabled:
            return

        recording_mics = [
            mic for mic in self._repo.list_bluetooth_mics() if self._mic_eligible_for_chunk(mic)
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
        mic_by_mac = {mic.mac_address: mic for mic in self._repo.list_bluetooth_mics()}
        try:
            while session.running:
                ts = time.time()
                for mac, writer in session.writers.items():
                    mic = mic_by_mac.get(mac)
                    if mic is None or not self._should_write_samples(mic, ts):
                        continue
                    writer.write_samples(ts)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def finalize_chunk(self, chunk_id: str) -> SourceManifest | None:
        session = self._sessions.pop(chunk_id, None)
        if session is None:
            mics = self._repo.list_bluetooth_mics()
            if not any(self._mic_eligible_for_chunk(m) for m in mics):
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
