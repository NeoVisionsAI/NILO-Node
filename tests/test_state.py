"""Tests for SQLite schema and repository."""

import json
from datetime import datetime, timezone
from pathlib import Path

from nilo_node.monitoring.models import AlwaysSchedule, Campaign, CampaignStatus
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository


def test_database_migrations_and_campaign_persistence(tmp_path: Path) -> None:
    db = Database(tmp_path / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    campaign = Campaign(
        campaign_id="camp-1",
        campaign_name="pruebas_dolor",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        schedule=AlwaysSchedule(),
    )
    repo.upsert_campaign(campaign)

    loaded = repo.get_active_campaign()
    assert loaded is not None
    assert loaded.campaign_name == "pruebas_dolor"
    assert loaded.subject_user_id is None

    conn = db.connect()
    row = conn.execute(
        "SELECT subject_user_id, config_snapshot FROM campaigns WHERE campaign_id = ?",
        ("camp-1",),
    ).fetchone()
    assert row is not None
    assert row["subject_user_id"] is None
    snapshot = json.loads(row["config_snapshot"])
    assert "subject_user_id" in snapshot

    db.close()
