from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.storage.sqlite import SQLiteStore


class StorageMigrationTests(unittest.TestCase):
    def test_initialize_adds_realtime_columns_and_detected_event_table_to_old_db(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "app.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE glucose_points (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        value REAL NOT NULL,
                        unit TEXT NOT NULL,
                        value_mg_dl REAL NOT NULL,
                        value_mmol_l REAL NOT NULL,
                        source TEXT NOT NULL,
                        quality_flag TEXT NOT NULL,
                        trend TEXT NOT NULL,
                        device_id TEXT,
                        session_id TEXT,
                        raw_record_id TEXT,
                        created_at TEXT NOT NULL,
                        UNIQUE(user_id, timestamp, source)
                    )
                    """
                )

            store = SQLiteStore(db_path)
            store.initialize()
            with store.connect() as conn:
                glucose_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(glucose_points)")
                }
                detected_table = conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'detected_glucose_events'
                    """
                ).fetchone()

        self.assertIn("received_at", glucose_columns)
        self.assertIsNotNone(detected_table)


if __name__ == "__main__":
    unittest.main()
