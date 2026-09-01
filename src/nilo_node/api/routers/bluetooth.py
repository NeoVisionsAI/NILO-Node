"""Bluetooth mic management API."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.bluetooth.models import normalize_mac
from nilo_node.config.models import AppConfig


class ConnectRequest(BaseModel):
    mac_address: str
    device_name: str | None = None


class RecordingRequest(BaseModel):
    record_enabled: bool = Field(description="Whether this mic should be recorded in chunks")


class MicSettingsRequest(BaseModel):
    display_name: str | None = None
    recording_mode: Literal["continuous", "interval", "on_demand"] | None = None
    recording_interval_sec: int | None = Field(default=None, ge=5, le=3600)
    record_enabled: bool | None = None
    recording_active: bool | None = None


class TestRecordingRequest(BaseModel):
    duration_sec: float = Field(default=10.0, ge=1.0, le=30.0)


def create_bluetooth_router(config: AppConfig, bluetooth: BluetoothManager) -> APIRouter:
    router = APIRouter(prefix="/api/v1/bluetooth", tags=["bluetooth"])
    auth = Depends(require_auth(config))

    @router.get("/discover", dependencies=[auth])
    async def discover_devices() -> dict[str, Any]:
        devices = await bluetooth.discover()
        status_obj = bluetooth.get_status()
        known = {mic.mac_address: mic for mic in status_obj.mics}
        enriched = []
        for device in devices:
            payload = device.model_dump()
            mic = known.get(device.mac_address)
            if mic is not None:
                payload["display_name"] = mic.display_name
                payload["label"] = mic.label
            else:
                payload["label"] = device.name or device.mac_address
            enriched.append(payload)
        return {
            "mock": status_obj.mock,
            "scan_timeout_sec": config.bluetooth.scan_timeout_sec,
            "devices": enriched,
        }

    @router.get("/status", dependencies=[auth])
    async def bluetooth_status() -> dict[str, Any]:
        if not bluetooth.get_status().mock:
            await bluetooth.sync_connection_state()
        return bluetooth.get_status().model_dump(mode="json")

    @router.post("/connect", dependencies=[auth])
    async def connect_mic(body: ConnectRequest) -> dict[str, Any]:
        try:
            record = await bluetooth.connect(body.mac_address, body.device_name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.post("/disconnect", dependencies=[auth])
    async def disconnect_mic(body: ConnectRequest) -> dict[str, Any]:
        try:
            record = await bluetooth.disconnect(body.mac_address)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.post("/unpair", dependencies=[auth])
    async def unpair_mic(body: ConnectRequest) -> dict[str, Any]:
        try:
            await bluetooth.unpair(body.mac_address)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return {"ok": True, "mac_address": normalize_mac(body.mac_address)}

    @router.patch("/mics/{mac_address}", dependencies=[auth])
    async def update_mic_settings(mac_address: str, body: MicSettingsRequest) -> dict[str, Any]:
        try:
            record = bluetooth.update_mic_settings(
                mac_address,
                display_name=body.display_name,
                recording_mode=body.recording_mode,
                recording_interval_sec=body.recording_interval_sec,
                record_enabled=body.record_enabled,
                recording_active=body.recording_active,
            )
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.patch("/mics/{mac_address}/recording", dependencies=[auth])
    async def set_mic_recording(mac_address: str, body: RecordingRequest) -> dict[str, Any]:
        try:
            record = bluetooth.set_recording(mac_address, body.record_enabled)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @router.post("/mics/{mac_address}/test-recording", dependencies=[auth])
    async def mic_test_recording(
        mac_address: str,
        body: TestRecordingRequest | None = None,
    ) -> dict[str, Any]:
        duration = body.duration_sec if body else 10.0
        try:
            result = await bluetooth.record_test_sample(mac_address, duration_sec=duration)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        recording_id = str(result["recording_id"])
        result["playback_url"] = f"/api/v1/bluetooth/test-recordings/{recording_id}"
        return result

    @router.get("/test-recordings/{recording_id}", dependencies=[auth])
    async def get_test_recording(recording_id: str) -> FileResponse:
        try:
            path = bluetooth.resolve_test_recording(recording_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    return router
