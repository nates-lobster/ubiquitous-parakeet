"""SQLite persistence for interaction cooldowns and simple event metrics."""

from __future__ import annotations

from contextlib import closing
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def utc_timestamp(value: datetime | None = None) -> str:
    """Return an ISO-8601 UTC timestamp suitable for lexical ordering."""
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: str | Path) -> Path:
    """Create or upgrade the local database without replacing existing data."""
    path = Path(database_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trigger_type TEXT NOT NULL CHECK (trigger_type IN ('proactive', 'reactive')),
                topic_category TEXT NOT NULL,
                engagement_score INTEGER CHECK (
                    engagement_score IS NULL OR engagement_score BETWEEN 0 AND 5
                )
            );
            CREATE INDEX IF NOT EXISTS idx_interactions_timestamp
                ON interactions(timestamp);
            CREATE INDEX IF NOT EXISTS idx_interactions_trigger_timestamp
                ON interactions(trigger_type, timestamp DESC);

            CREATE TABLE IF NOT EXISTS vitals_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vitals_metric_timestamp
                ON vitals_log(metric_type, timestamp DESC);
            """
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return path


def latest_proactive_timestamp(database_path: str | Path) -> str | None:
    path = Path(database_path).expanduser()
    if not path.exists():
        return None
    with closing(connect(path)) as connection, connection:
        row = connection.execute(
            "SELECT timestamp FROM interactions "
            "WHERE trigger_type = 'proactive' ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone()
    return str(row["timestamp"]) if row else None


def record_interaction(
    database_path: str | Path,
    *,
    trigger_type: str,
    topic_category: str,
    engagement_score: int | None = None,
    timestamp: datetime | None = None,
) -> int:
    if trigger_type not in {"proactive", "reactive"}:
        raise ValueError("trigger_type must be 'proactive' or 'reactive'")
    if not topic_category.strip():
        raise ValueError("topic_category must not be empty")
    path = initialize_database(database_path)
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO interactions(timestamp, trigger_type, topic_category, engagement_score) "
            "VALUES (?, ?, ?, ?)",
            (utc_timestamp(timestamp), trigger_type, topic_category.strip(), engagement_score),
        )
        return int(cursor.lastrowid)


def record_vital(
    database_path: str | Path,
    *,
    metric_type: str,
    value: Any,
    timestamp: datetime | None = None,
) -> int:
    if not metric_type.strip():
        raise ValueError("metric_type must not be empty")
    path = initialize_database(database_path)
    with closing(connect(path)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO vitals_log(timestamp, metric_type, value) VALUES (?, ?, ?)",
            (utc_timestamp(timestamp), metric_type.strip(), str(value)),
        )
        return int(cursor.lastrowid)
