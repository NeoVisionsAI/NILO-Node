"""Repository layer for campaigns, runs, and chunks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from nilo_node.monitoring.models import Campaign, ChunkRecord, RecordingRun
from nilo_node.storage.models import ChunkQuery
from nilo_node.state.database import Database


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class StateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_campaign(self, campaign: Campaign) -> None:
        conn = self._db.connect()
        now = _utc_now_iso()
        snapshot = campaign.model_dump(mode="json")
        conn.execute(
            """
            INSERT INTO campaigns (
                campaign_id, campaign_name, subject_user_id, status,
                valid_from, valid_until, config_snapshot, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
                campaign_name = excluded.campaign_name,
                subject_user_id = excluded.subject_user_id,
                status = excluded.status,
                valid_from = excluded.valid_from,
                valid_until = excluded.valid_until,
                config_snapshot = excluded.config_snapshot,
                updated_at = excluded.updated_at
            """,
            (
                campaign.campaign_id,
                campaign.campaign_name,
                campaign.subject_user_id,
                campaign.status.value,
                campaign.valid_from.isoformat() if campaign.valid_from else None,
                campaign.valid_until.isoformat() if campaign.valid_until else None,
                json.dumps(snapshot),
                now,
                now,
            ),
        )
        conn.commit()

    def get_active_campaign(self) -> Campaign | None:
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT config_snapshot FROM campaigns
            WHERE status IN ('active', 'paused', 'scheduled')
            ORDER BY updated_at DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["config_snapshot"])
        return Campaign.model_validate(data)

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT config_snapshot FROM campaigns WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["config_snapshot"])
        return Campaign.model_validate(data)

    def create_recording_run(self, run: RecordingRun) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO recording_runs (
                recording_run_id, campaign_id, campaign_name, subject_user_id,
                node_id, start_ts, end_ts, path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.recording_run_id,
                run.campaign_id,
                run.campaign_name,
                run.subject_user_id,
                run.node_id,
                run.start_ts.isoformat(),
                run.end_ts.isoformat() if run.end_ts else None,
                run.path,
            ),
        )
        conn.commit()

    def close_recording_run(self, recording_run_id: str, end_ts: datetime) -> None:
        conn = self._db.connect()
        conn.execute(
            "UPDATE recording_runs SET end_ts = ? WHERE recording_run_id = ?",
            (end_ts.isoformat(), recording_run_id),
        )
        conn.commit()

    def get_open_recording_run(self, campaign_id: str) -> RecordingRun | None:
        conn = self._db.connect()
        row = conn.execute(
            """
            SELECT * FROM recording_runs
            WHERE campaign_id = ? AND end_ts IS NULL
            ORDER BY start_ts DESC LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        return RecordingRun(
            recording_run_id=row["recording_run_id"],
            campaign_id=row["campaign_id"],
            campaign_name=row["campaign_name"],
            subject_user_id=row["subject_user_id"],
            node_id=row["node_id"],
            start_ts=_parse_dt(row["start_ts"]),  # type: ignore[arg-type]
            end_ts=_parse_dt(row["end_ts"]),
            path=row["path"],
        )

    def get_recording_run(self, recording_run_id: str) -> RecordingRun | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM recording_runs WHERE recording_run_id = ?",
            (recording_run_id,),
        ).fetchone()
        if row is None:
            return None
        return RecordingRun(
            recording_run_id=row["recording_run_id"],
            campaign_id=row["campaign_id"],
            campaign_name=row["campaign_name"],
            subject_user_id=row["subject_user_id"],
            node_id=row["node_id"],
            start_ts=_parse_dt(row["start_ts"]),  # type: ignore[arg-type]
            end_ts=_parse_dt(row["end_ts"]),
            path=row["path"],
        )

    def insert_chunk(self, chunk: ChunkRecord) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO chunks (
                chunk_id, campaign_id, campaign_name, recording_run_id,
                subject_user_id, node_id, start_ts, end_ts, path, status,
                sources_present, byte_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_id,
                chunk.campaign_id,
                chunk.campaign_name,
                chunk.recording_run_id,
                chunk.subject_user_id,
                chunk.node_id,
                chunk.start_ts.isoformat(),
                chunk.end_ts.isoformat(),
                chunk.path,
                chunk.status,
                json.dumps(chunk.sources_present),
                chunk.byte_size,
            ),
        )
        conn.commit()

    def update_chunk_status(
        self,
        chunk_id: str,
        status: str,
        sources_present: list[str],
        byte_size: int,
    ) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            UPDATE chunks
            SET status = ?, sources_present = ?, byte_size = ?
            WHERE chunk_id = ?
            """,
            (status, json.dumps(sources_present), byte_size, chunk_id),
        )
        conn.commit()

    def _row_to_chunk(self, row: Any) -> ChunkRecord:
        import json as _json

        return ChunkRecord(
            chunk_id=row["chunk_id"],
            campaign_id=row["campaign_id"],
            campaign_name=row["campaign_name"],
            recording_run_id=row["recording_run_id"],
            subject_user_id=row["subject_user_id"],
            node_id=row["node_id"],
            start_ts=_parse_dt(row["start_ts"]),  # type: ignore[arg-type]
            end_ts=_parse_dt(row["end_ts"]),  # type: ignore[arg-type]
            path=row["path"],
            status=row["status"],
            sources_present=_json.loads(row["sources_present"]),
            byte_size=row["byte_size"],
        )

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        conn = self._db.connect()
        row = conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_chunk(row)

    def list_chunks(self, query: ChunkQuery) -> list[ChunkRecord]:
        conn = self._db.connect()
        clauses: list[str] = []
        params: list[Any] = []

        if query.status:
            clauses.append("status = ?")
            params.append(query.status)
        if query.campaign_id:
            clauses.append("campaign_id = ?")
            params.append(query.campaign_id)
        if query.campaign_name:
            clauses.append("campaign_name = ?")
            params.append(query.campaign_name)
        if query.subject_user_id:
            clauses.append("subject_user_id = ?")
            params.append(query.subject_user_id)
        if query.start_ts is not None:
            clauses.append("end_ts > ?")
            params.append(query.start_ts.isoformat())
        if query.end_ts is not None:
            clauses.append("start_ts < ?")
            params.append(query.end_ts.isoformat())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM chunks {where}
            ORDER BY start_ts ASC
            LIMIT ? OFFSET ?
        """
        params.extend([query.limit, query.offset])
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def mark_chunk_deleted(self, chunk_id: str) -> None:
        conn = self._db.connect()
        conn.execute(
            "UPDATE chunks SET status = 'deleted' WHERE chunk_id = ?",
            (chunk_id,),
        )
        conn.commit()

    def chunk_storage_stats(self) -> dict[str, Any]:
        conn = self._db.connect()
        total = conn.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(byte_size), 0) AS bytes
            FROM chunks WHERE status = 'complete'
            """
        ).fetchone()
        by_campaign = conn.execute(
            """
            SELECT campaign_name, COUNT(*) AS count, COALESCE(SUM(byte_size), 0) AS bytes
            FROM chunks WHERE status = 'complete'
            GROUP BY campaign_name
            ORDER BY bytes DESC
            """
        ).fetchall()
        return {
            "complete_chunks": total["count"],
            "complete_bytes": total["bytes"],
            "by_campaign": [
                {"campaign_name": r["campaign_name"], "count": r["count"], "bytes": r["bytes"]}
                for r in by_campaign
            ],
        }

    def insert_replication_job(
        self,
        job_id: str,
        chunk_id: str,
        target_id: str,
        status: str = "pending",
    ) -> None:
        now = _utc_now_iso()
        conn = self._db.connect()
        conn.execute(
            """
            INSERT OR IGNORE INTO replication_jobs (
                job_id, chunk_id, target_id, status, attempts,
                last_error, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, 0, NULL, ?, ?, NULL)
            """,
            (job_id, chunk_id, target_id, status, now, now),
        )
        conn.commit()

    def fetch_pending_replication_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM replication_jobs
            WHERE status IN ('pending', 'failed')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_replication_job(
        self,
        job_id: str,
        status: str,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        now = _utc_now_iso()
        conn = self._db.connect()
        if increment_attempts:
            conn.execute(
                """
                UPDATE replication_jobs
                SET status = ?, last_error = ?, updated_at = ?,
                    attempts = attempts + 1,
                    completed_at = CASE WHEN ? = 'complete' THEN ? ELSE completed_at END
                WHERE job_id = ?
                """,
                (status, last_error, now, status, now, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE replication_jobs
                SET status = ?, last_error = ?, updated_at = ?,
                    completed_at = CASE WHEN ? = 'complete' THEN ? ELSE completed_at END
                WHERE job_id = ?
                """,
                (status, last_error, now, status, now, job_id),
            )
        conn.commit()

    def upsert_chunk_replication(
        self,
        chunk_id: str,
        target_id: str,
        status: str,
        replicated_at: str | None = None,
    ) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO chunk_replication (chunk_id, target_id, status, replicated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chunk_id, target_id) DO UPDATE SET
                status = excluded.status,
                replicated_at = excluded.replicated_at
            """,
            (chunk_id, target_id, status, replicated_at),
        )
        conn.commit()

    def chunk_fully_replicated(self, chunk_id: str, target_ids: list[str]) -> bool:
        if not target_ids:
            return True
        conn = self._db.connect()
        placeholders = ",".join("?" for _ in target_ids)
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n FROM chunk_replication
            WHERE chunk_id = ? AND target_id IN ({placeholders}) AND status = 'complete'
            """,
            [chunk_id, *target_ids],
        ).fetchone()
        return row["n"] == len(target_ids)

    def insert_upload_job(
        self,
        chunk_id: str,
        job_type: str,
        *,
        status: str = "pending",
    ) -> str:
        from ulid import ULID

        job_id = str(ULID())
        now = _utc_now_iso()
        conn = self._db.connect()
        conn.execute(
            """
            INSERT OR IGNORE INTO upload_queue (
                job_id, chunk_id, job_type, status, payload,
                attempts, last_error, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, '{}', 0, NULL, ?, ?, NULL)
            """,
            (job_id, chunk_id, job_type, status, now, now),
        )
        conn.commit()
        return job_id

    def fetch_pending_upload_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT * FROM upload_queue
            WHERE status IN ('pending', 'failed')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_upload_job(
        self,
        job_id: str,
        status: str,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        now = _utc_now_iso()
        conn = self._db.connect()
        if increment_attempts:
            conn.execute(
                """
                UPDATE upload_queue
                SET status = ?, last_error = ?, updated_at = ?,
                    attempts = attempts + 1,
                    completed_at = CASE WHEN ? = 'complete' THEN ? ELSE completed_at END
                WHERE job_id = ?
                """,
                (status, last_error, now, status, now, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE upload_queue
                SET status = ?, last_error = ?, updated_at = ?,
                    completed_at = CASE WHEN ? = 'complete' THEN ? ELSE completed_at END
                WHERE job_id = ?
                """,
                (status, last_error, now, status, now, job_id),
            )
        conn.commit()

    def upload_queue_stats(self) -> dict[str, int]:
        conn = self._db.connect()
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM upload_queue
            GROUP BY status
            """
        ).fetchall()
        stats = {"pending": 0, "in_progress": 0, "complete": 0, "failed": 0}
        for row in rows:
            stats[row["status"]] = row["count"]
        stats["total"] = sum(stats.values())
        return stats

    def save_heartbeat(self, payload: dict[str, Any]) -> None:
        conn = self._db.connect()
        conn.execute(
            "INSERT INTO heartbeats (payload, created_at) VALUES (?, ?)",
            (json.dumps(payload), _utc_now_iso()),
        )
        conn.commit()

    def save_config_snapshot(self, payload: dict[str, Any]) -> None:
        conn = self._db.connect()
        conn.execute(
            "INSERT INTO backend_config_snapshots (payload, created_at) VALUES (?, ?)",
            (json.dumps(payload), _utc_now_iso()),
        )
        conn.commit()

    def get_open_chunk(self) -> ChunkRecord | None:
        conn = self._db.fresh_connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM chunks
                WHERE status = 'open'
                ORDER BY start_ts DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_chunk(row)

    def get_latest_chunk(self) -> ChunkRecord | None:
        conn = self._db.fresh_connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM chunks
                WHERE status IN ('open', 'complete')
                ORDER BY start_ts DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_chunk(row)

    def find_chunk_for_timestamp(self, ts: datetime) -> ChunkRecord | None:
        conn = self._db.fresh_connect()
        iso = ts.isoformat()
        try:
            row = conn.execute(
                """
                SELECT * FROM chunks
                WHERE start_ts <= ? AND end_ts > ?
                  AND status IN ('open', 'complete')
                ORDER BY start_ts DESC
                LIMIT 1
                """,
                (iso, iso),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return self._row_to_chunk(row)

    def upsert_cardmed_assignment(self, assignment: "CardmedAssignment") -> None:
        from nilo_node.cardmed.models import CardmedAssignment as _CA

        assert isinstance(assignment, _CA)
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO cardmed_assignments (
                device_id, node_id, device_name, mac_address,
                registered_at, last_seen_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                node_id = excluded.node_id,
                device_name = excluded.device_name,
                mac_address = excluded.mac_address,
                last_seen_at = excluded.last_seen_at,
                metadata = excluded.metadata
            """,
            (
                assignment.device_id,
                assignment.node_id,
                assignment.device_name,
                assignment.mac_address,
                assignment.registered_at.isoformat(),
                assignment.last_seen_at.isoformat(),
                json.dumps(assignment.metadata),
            ),
        )
        conn.commit()

    def get_cardmed_assignment(self, device_id: str) -> "CardmedAssignment | None":
        from nilo_node.cardmed.models import CardmedAssignment

        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM cardmed_assignments WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        return CardmedAssignment(
            device_id=row["device_id"],
            node_id=row["node_id"],
            device_name=row["device_name"],
            mac_address=row["mac_address"],
            registered_at=datetime.fromisoformat(row["registered_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
            metadata=json.loads(row["metadata"]),
        )

    def list_cardmed_assignments(self) -> list["CardmedAssignment"]:
        from nilo_node.cardmed.models import CardmedAssignment

        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM cardmed_assignments ORDER BY registered_at ASC"
        ).fetchall()
        return [
            CardmedAssignment(
                device_id=row["device_id"],
                node_id=row["node_id"],
                device_name=row["device_name"],
                mac_address=row["mac_address"],
                registered_at=datetime.fromisoformat(row["registered_at"]),
                last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    def delete_cardmed_assignment(self, device_id: str) -> bool:
        conn = self._db.connect()
        cursor = conn.execute(
            "DELETE FROM cardmed_assignments WHERE device_id = ?",
            (device_id,),
        )
        conn.commit()
        return cursor.rowcount > 0

    def touch_cardmed_assignment(self, device_id: str) -> None:
        conn = self._db.connect()
        conn.execute(
            "UPDATE cardmed_assignments SET last_seen_at = ? WHERE device_id = ?",
            (_utc_now_iso(), device_id),
        )
        conn.commit()

    def upsert_device(
        self,
        device_id: str,
        device_type: str,
        status: str,
        metadata: dict[str, Any],
    ) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO devices (device_id, device_type, status, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                device_type = excluded.device_type,
                status = excluded.status,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (device_id, device_type, status, json.dumps(metadata), _utc_now_iso()),
        )
        conn.commit()

    def list_devices(self, device_type: str | None = None) -> list[dict[str, Any]]:
        conn = self._db.connect()
        if device_type:
            rows = conn.execute(
                "SELECT * FROM devices WHERE device_type = ? ORDER BY updated_at DESC",
                (device_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "device_id": row["device_id"],
                "device_type": row["device_type"],
                "status": row["status"],
                "metadata": json.loads(row["metadata"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def insert_device_event(
        self,
        device_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO device_events (device_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (device_id, event_type, json.dumps(payload), _utc_now_iso()),
        )
        conn.commit()

    def upsert_bluetooth_mic(
        self,
        mac_address: str,
        *,
        device_name: str | None,
        connected: bool,
        record_enabled: bool,
        paired: bool,
    ) -> "BluetoothMicRecord":
        from nilo_node.bluetooth.models import normalize_mac

        mac = normalize_mac(mac_address)
        now = _utc_now_iso()
        conn = self._db.connect()
        existing = conn.execute(
            "SELECT registered_at FROM bluetooth_mics WHERE mac_address = ?",
            (mac,),
        ).fetchone()
        registered_at = existing["registered_at"] if existing else now
        conn.execute(
            """
            INSERT INTO bluetooth_mics (
                mac_address, device_name, connected, record_enabled, paired,
                registered_at, last_seen_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
            ON CONFLICT(mac_address) DO UPDATE SET
                device_name = COALESCE(excluded.device_name, bluetooth_mics.device_name),
                connected = excluded.connected,
                record_enabled = excluded.record_enabled,
                paired = excluded.paired,
                last_seen_at = excluded.last_seen_at
            """,
            (
                mac,
                device_name,
                int(connected),
                int(record_enabled),
                int(paired),
                registered_at,
                now,
            ),
        )
        conn.commit()
        record = self.get_bluetooth_mic(mac)
        assert record is not None
        return record

    def get_bluetooth_mic(self, mac_address: str) -> "BluetoothMicRecord | None":
        from nilo_node.bluetooth.models import normalize_mac

        mac = normalize_mac(mac_address)
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM bluetooth_mics WHERE mac_address = ?",
            (mac,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_bluetooth_mic(row)

    def list_bluetooth_mics(self) -> list["BluetoothMicRecord"]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM bluetooth_mics ORDER BY registered_at ASC"
        ).fetchall()
        return [self._row_to_bluetooth_mic(row) for row in rows]

    def set_bluetooth_mic_recording(
        self,
        mac_address: str,
        record_enabled: bool,
    ) -> "BluetoothMicRecord | None":
        from nilo_node.bluetooth.models import normalize_mac

        mac = normalize_mac(mac_address)
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM bluetooth_mics WHERE mac_address = ?",
            (mac,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE bluetooth_mics
            SET record_enabled = ?, last_seen_at = ?
            WHERE mac_address = ?
            """,
            (int(record_enabled), _utc_now_iso(), mac),
        )
        conn.commit()
        return self.get_bluetooth_mic(mac)

    def touch_bluetooth_mic_seen(self, mac_address: str, device_name: str | None) -> None:
        from nilo_node.bluetooth.models import normalize_mac

        mac = normalize_mac(mac_address)
        now = _utc_now_iso()
        conn = self._db.connect()
        conn.execute(
            """
            INSERT INTO bluetooth_mics (
                mac_address, device_name, connected, record_enabled, paired,
                registered_at, last_seen_at, metadata
            ) VALUES (?, ?, 0, 1, 0, ?, ?, '{}')
            ON CONFLICT(mac_address) DO UPDATE SET
                device_name = COALESCE(excluded.device_name, bluetooth_mics.device_name),
                last_seen_at = excluded.last_seen_at
            """,
            (mac, device_name, now, now),
        )
        conn.commit()

    @staticmethod
    def _row_to_bluetooth_mic(row: Any) -> "BluetoothMicRecord":
        from nilo_node.bluetooth.models import BluetoothMicRecord

        return BluetoothMicRecord(
            mac_address=row["mac_address"],
            device_name=row["device_name"],
            connected=bool(row["connected"]),
            record_enabled=bool(row["record_enabled"]),
            paired=bool(row["paired"]),
            registered_at=datetime.fromisoformat(row["registered_at"]),
            last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        )
