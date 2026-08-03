"""Bluetooth mic management API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nilo_node.api.deps import require_auth
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.config.models import AppConfig


class ConnectRequest(BaseModel):
    mac_address: str
    device_name: str | None = None


class RecordingRequest(BaseModel):
    record_enabled: bool = Field(description="Whether this mic should be recorded in chunks")


def create_bluetooth_router(config: AppConfig, bluetooth: BluetoothManager) -> APIRouter:
    router = APIRouter(prefix="/api/v1/bluetooth", tags=["bluetooth"])
    auth = Depends(require_auth(config))

    @router.get("/discover", dependencies=[auth])
    async def discover_devices() -> dict[str, Any]:
        devices = await bluetooth.discover()
        return {
            "mock": bluetooth.get_status().mock,
            "devices": [d.model_dump() for d in devices],
        }

    @router.get("/status", dependencies=[auth])
    async def bluetooth_status() -> dict[str, Any]:
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

    @router.patch("/mics/{mac_address}/recording", dependencies=[auth])
    async def set_mic_recording(mac_address: str, body: RecordingRequest) -> dict[str, Any]:
        try:
            record = bluetooth.set_recording(mac_address, body.record_enabled)
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    return router
