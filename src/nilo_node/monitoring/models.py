"""Campaign and schedule domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CampaignStatus(str, Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScheduleWindow(BaseModel):
    start: str
    end: str


class WeeklyRule(BaseModel):
    days: list[str]
    windows: list[ScheduleWindow]


class FixedWindowSchedule(BaseModel):
    mode: Literal["fixed_window"] = "fixed_window"
    start: datetime
    end: datetime


class WeeklySchedule(BaseModel):
    mode: Literal["weekly"] = "weekly"
    rules: list[WeeklyRule]


class AlwaysSchedule(BaseModel):
    mode: Literal["always"] = "always"


Schedule = FixedWindowSchedule | WeeklySchedule | AlwaysSchedule


class SourceToggle(BaseModel):
    enabled: bool = True
    record_video: bool | None = None
    record_depth: bool | None = None
    record_landmarks: bool | None = None


class Campaign(BaseModel):
    """Campaign from NILO-backend. subject_user_id is always present, may be null."""

    campaign_id: str
    campaign_name: str
    subject_user_id: str | None = Field(
        ...,
        description="Patient/user ID; null when no patient assigned yet.",
    )
    status: CampaignStatus
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    chunk_duration_sec: int = 300
    timezone: str = "UTC"
    schedule: Schedule
    sources: dict[str, SourceToggle] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _ensure_subject_user_id_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "subject_user_id" not in data:
            data["subject_user_id"] = None
        return data


class RecordingRun(BaseModel):
    recording_run_id: str
    campaign_id: str
    campaign_name: str
    subject_user_id: str | None
    node_id: str
    start_ts: datetime
    end_ts: datetime | None = None
    path: str


class ChunkRecord(BaseModel):
    chunk_id: str
    campaign_id: str
    campaign_name: str
    recording_run_id: str
    subject_user_id: str | None
    node_id: str
    start_ts: datetime
    end_ts: datetime
    path: str
    status: Literal["open", "complete", "partial", "deleted"] = "open"
    sources_present: list[str] = Field(default_factory=list)
    byte_size: int = 0
