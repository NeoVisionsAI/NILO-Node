"""Tests for schedule evaluation."""

from datetime import datetime, timezone

from nilo_node.monitoring.models import (
    AlwaysSchedule,
    Campaign,
    CampaignStatus,
    FixedWindowSchedule,
    ScheduleWindow,
    WeeklyRule,
    WeeklySchedule,
)
from nilo_node.monitoring.schedule import is_capture_active


def _campaign(schedule: AlwaysSchedule | FixedWindowSchedule | WeeklySchedule) -> Campaign:
    return Campaign(
        campaign_id="c1",
        campaign_name="test",
        subject_user_id="patient-1",
        status=CampaignStatus.ACTIVE,
        timezone="UTC",
        schedule=schedule,
    )


def test_always_schedule_active() -> None:
    campaign = _campaign(AlwaysSchedule())
    now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
    assert is_capture_active(campaign, now) is True


def test_fixed_window_inside() -> None:
    start = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    campaign = _campaign(FixedWindowSchedule(start=start, end=end))
    now = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    assert is_capture_active(campaign, now) is True


def test_fixed_window_outside() -> None:
    start = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
    campaign = _campaign(FixedWindowSchedule(start=start, end=end))
    now = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
    assert is_capture_active(campaign, now) is False


def test_weekly_schedule_active_on_matching_day() -> None:
    schedule = WeeklySchedule(
        rules=[
            WeeklyRule(
                days=["mon"],
                windows=[ScheduleWindow(start="10:00", end="22:00")],
            )
        ]
    )
    campaign = _campaign(schedule)
    # 2026-08-03 is a Monday
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    assert is_capture_active(campaign, now) is True


def test_weekly_schedule_inactive_on_empty_windows() -> None:
    schedule = WeeklySchedule(
        rules=[WeeklyRule(days=["wed"], windows=[])]
    )
    campaign = _campaign(schedule)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)  # Wednesday
    assert is_capture_active(campaign, now) is False


def test_paused_campaign_inactive() -> None:
    campaign = Campaign(
        campaign_id="c1",
        campaign_name="test",
        subject_user_id=None,
        status=CampaignStatus.PAUSED,
        timezone="UTC",
        schedule=AlwaysSchedule(),
    )
    now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    assert is_capture_active(campaign, now) is False
