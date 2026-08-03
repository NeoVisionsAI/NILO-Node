"""SQLite database schema and connection management."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 5

MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        campaign_id TEXT PRIMARY KEY,
        campaign_name TEXT NOT NULL,
        subject_user_id TEXT,
        status TEXT NOT NULL,
        valid_from TEXT,
        valid_until TEXT,
        config_snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS recording_runs (
        recording_run_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        campaign_name TEXT NOT NULL,
        subject_user_id TEXT,
        node_id TEXT NOT NULL,
        start_ts TEXT NOT NULL,
        end_ts TEXT,
        path TEXT NOT NULL,
        FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        campaign_name TEXT NOT NULL,
        recording_run_id TEXT NOT NULL,
        subject_user_id TEXT,
        node_id TEXT NOT NULL,
        start_ts TEXT NOT NULL,
        end_ts TEXT NOT NULL,
        path TEXT NOT NULL,
        status TEXT NOT NULL,
        sources_present TEXT NOT NULL DEFAULT '[]',
        byte_size INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (recording_run_id) REFERENCES recording_runs(recording_run_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_campaign ON chunks(campaign_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_subject ON chunks(subject_user_id);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_time ON chunks(start_ts, end_ts);
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        device_type TEXT NOT NULL,
        status TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS device_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS heartbeats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS backend_config_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS replication_jobs (
        job_id TEXT PRIMARY KEY,
        chunk_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_replication_jobs_status ON replication_jobs(status);
    """,
    """
    CREATE TABLE IF NOT EXISTS chunk_replication (
        chunk_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        status TEXT NOT NULL,
        replicated_at TEXT,
        PRIMARY KEY (chunk_id, target_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS cardmed_assignments (
        device_id TEXT PRIMARY KEY,
        node_id TEXT NOT NULL,
        device_name TEXT,
        mac_address TEXT,
        registered_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS bluetooth_mics (
        mac_address TEXT PRIMARY KEY,
        device_name TEXT,
        connected INTEGER NOT NULL DEFAULT 0,
        record_enabled INTEGER NOT NULL DEFAULT 1,
        paired INTEGER NOT NULL DEFAULT 0,
        registered_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS upload_queue (
        job_id TEXT PRIMARY KEY,
        chunk_id TEXT NOT NULL,
        job_type TEXT NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status);
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return conn

    def fresh_connect(self) -> sqlite3.Connection:
        """Short-lived connection for cross-thread reads (API worker threads)."""
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def migrate(self) -> None:
        conn = self.connect()
        for statement in MIGRATIONS:
            conn.executescript(statement)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
        else:
            conn.execute(
                "UPDATE schema_version SET version = ?",
                (SCHEMA_VERSION,),
            )
        conn.commit()

    def close(self) -> None:
        with self._connections_lock:
            for conn in self._connections:
                conn.close()
            self._connections.clear()
        if hasattr(self._local, "conn"):
            del self._local.conn
