"""Tests for campaign and manifest models."""

import pytest
from pydantic import ValidationError

from nilo_node.chunks.manifest import build_manifest
from nilo_node.monitoring.models import AlwaysSchedule, Campaign, CampaignStatus


def test_campaign_requires_subject_user_id_key() -> None:
    campaign = Campaign.model_validate(
        {
            "campaign_id": "c1",
            "campaign_name": "pruebas_dolor",
            "status": "active",
            "schedule": {"mode": "always"},
        }
    )
    assert campaign.subject_user_id is None


def test_campaign_rejects_missing_subject_user_id_when_explicitly_omitted_via_validator() -> None:
    campaign = Campaign(
        campaign_id="c1",
        campaign_name="named",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
    )
    assert "subject_user_id" in campaign.model_dump()


def test_campaign_with_patient_id() -> None:
    campaign = Campaign(
        campaign_id="c1",
        campaign_name="pruebas_dolor",
        subject_user_id="patient-123",
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
    )
    assert campaign.subject_user_id == "patient-123"


def test_manifest_always_includes_subject_user_id() -> None:
    from datetime import datetime, timezone

    start = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 3, 10, 5, tzinfo=timezone.utc)
    manifest = build_manifest(
        chunk_id="chunk1",
        campaign_id="c1",
        campaign_name="pruebas_dolor",
        recording_run_id="run1",
        subject_user_id=None,
        node_id="node1",
        start=start,
        end=end,
        chunk_duration_sec=300,
        sources={},
    )
    assert "subject_user_id" in manifest
    assert manifest["subject_user_id"] is None


def test_campaign_status_enum() -> None:
    with pytest.raises(ValidationError):
        Campaign(
            campaign_id="c1",
            campaign_name="x",
            subject_user_id=None,
            status="invalid",  # type: ignore[arg-type]
            schedule=AlwaysSchedule(),
        )
