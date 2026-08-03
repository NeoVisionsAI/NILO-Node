"""Camera domain models."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class CameraConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class CameraDeviceInfo(BaseModel):
    device_id: str
    name: str = "OAK Camera"
    platform: str = ""
    protocol: str = ""
    state: str = "available"


class CaptureFlags(BaseModel):
    """Which camera streams to record for the current campaign/chunk."""

    rgb: bool = True
    tof: bool = True
    pose: bool = True

    @classmethod
    def from_campaign_sources(
        cls,
        sources: dict,
        *,
        defaults: CaptureFlags | None = None,
    ) -> CaptureFlags:
        base = defaults or CaptureFlags()

        def _stream_enabled(key: str, record_key: str, default: bool) -> bool:
            toggle = sources.get(key)
            if toggle is None:
                return default
            if isinstance(toggle, dict):
                if not toggle.get("enabled", default):
                    return False
                sub = toggle.get(record_key)
                return default if sub is None else bool(sub)
            if not getattr(toggle, "enabled", default):
                return False
            sub = getattr(toggle, record_key, None)
            return default if sub is None else bool(sub)

        return cls(
            rgb=_stream_enabled("rgb", "record_video", base.rgb),
            tof=_stream_enabled("tof", "record_depth", base.tof),
            pose=_stream_enabled("pose", "record_landmarks", base.pose),
        )


class CameraStatus(BaseModel):
    connection_state: CameraConnectionState
    connected_device_id: str | None = None
    recording: bool = False
    active_chunk_id: str | None = None
    capture_flags: CaptureFlags = Field(default_factory=CaptureFlags)
    pipeline_mode: Literal["depthai", "mock"] = "mock"
    available_devices: list[CameraDeviceInfo] = Field(default_factory=list)
    last_error: str | None = None
    depthai_available: bool = False
