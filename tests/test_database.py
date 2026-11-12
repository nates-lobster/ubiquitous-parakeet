import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ubiquitous_parakeet.database import (
    initialize_database,
    latest_proactive_timestamp,
    record_interaction,
    record_vital,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "data" / "companion.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_is_idempotent_and_has_expected_columns(self):
        initialize_database(self.db)
        initialize_database(self.db)
        with sqlite3.connect(self.db) as connection:
            interaction_columns = {row[1] for row in connection.execute("PRAGMA table_info(interactions)")}
            vital_columns = {row[1] for row in connection.execute("PRAGMA table_info(vitals_log)")}
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertTrue({"id", "timestamp", "trigger_type", "topic_category", "engagement_score"} <= interaction_columns)
        self.assertTrue({"id", "timestamp", "metric_type", "value"} <= vital_columns)
        self.assertEqual(version, 1)

    def test_records_interaction_and_metric(self):
        timestamp = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        record_interaction(self.db, trigger_type="proactive", topic_category="music", timestamp=timestamp)
        record_vital(self.db, metric_type="hydration_prompt", value="offered", timestamp=timestamp)
        self.assertEqual(latest_proactive_timestamp(self.db), "2026-01-02T03:04:00+00:00")
        with sqlite3.connect(self.db) as connection:
            value = connection.execute("SELECT value FROM vitals_log").fetchone()[0]
        self.assertEqual(value, "offered")

    def test_rejects_invalid_interaction_type(self):
        with self.assertRaises(ValueError):
            record_interaction(self.db, trigger_type="unknown", topic_category="test")


if __name__ == "__main__":
    unittest.main()
