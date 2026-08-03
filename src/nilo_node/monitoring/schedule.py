"""Schedule evaluation engine."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from nilo_node.monitoring.models import (
    AlwaysSchedule,
    Campaign,
    CampaignStatus,
    FixedWindowSchedule,
    WeeklySchedule,
)

_DAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _within_validity(campaign: Campaign, now: datetime) -> bool:
    if campaign.valid_from and now < campaign.valid_from:
        return False
    if campaign.valid_until and now > campaign.valid_until:
        return False
    return True


def _weekly_active(schedule: WeeklySchedule, local_now: datetime) -> bool:
    weekday = local_now.weekday()
    current = local_now.time()

    for rule in schedule.rules:
        rule_days = {_DAY_MAP[d.lower()] for d in rule.days if d.lower() in _DAY_MAP}
        if weekday not in rule_days:
            continue
        for window in rule.windows:
            start = _parse_hhmm(window.start)
            end = _parse_hhmm(window.end)
            if start <= end:
                if start <= current < end:
                    return True
            else:
                if current >= start or current < end:
                    return True
    return False


def is_capture_active(campaign: Campaign | None, now: datetime) -> bool:
    """Return True when recording should be ON for the given campaign."""
    if campaign is None:
        return False
    if campaign.status != CampaignStatus.ACTIVE:
        return False

    tz = ZoneInfo(campaign.timezone)
    local_now = now.astimezone(tz)
    if not _within_validity(campaign, local_now):
        return False

    schedule = campaign.schedule
    if isinstance(schedule, AlwaysSchedule):
        return True
    if isinstance(schedule, FixedWindowSchedule):
        return schedule.start <= now <= schedule.end
    if isinstance(schedule, WeeklySchedule):
        return _weekly_active(schedule, local_now)
    return False
