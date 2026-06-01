from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    DataScope,
    DeviceSession,
    GlucosePoint,
    ImportIssue,
    RawCGMRecord,
    RawImportBatch,
    TimeRange,
    UserEvent,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class CGMRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_status_reports_cgm_tables(self) -> None:
        status = self.repository.status()

        self.assertTrue(status.tables_present)
        self.assertEqual(status.table_count, 6)
        self.assertEqual(status.glucose_point_count, 0)

    def test_import_batch_round_trips_records_and_issues(self) -> None:
        batch = RawImportBatch(
            batch_id="batch-1",
            source_name="sample.csv",
            source_format="csv",
            records=[
                RawCGMRecord(
                    source_id="sample.csv",
                    source_format="csv",
                    raw_payload={"timestamp": "2026-05-31T00:00:00Z", "value": "108"},
                    row_number=1,
                    recorded_at=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                    value=108,
                    unit="mg/dL",
                )
            ],
            issues=[
                ImportIssue(
                    row_number=2,
                    field="value",
                    message="missing glucose value",
                    raw_record={"timestamp": "2026-05-31T00:05:00Z"},
                )
            ],
        )

        saved = self.repository.create_import_batch(batch)

        self.assertEqual(saved.batch_id, "batch-1")
        self.assertEqual(saved.record_count, 1)
        self.assertEqual(saved.issue_count, 1)
        self.assertEqual(saved.records[0].value, 108)
        self.assertEqual(saved.issues[0].field, "value")

    def test_glucose_points_query_by_user_window_and_source(self) -> None:
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                value=6.0,
                unit="mmol/L",
                source="sensor:a",
                quality_flag="valid",
            )
        )
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-1",
                timestamp=datetime(2026, 5, 31, 0, 5, tzinfo=timezone.utc),
                value=6.2,
                unit="mmol/L",
                source="sensor:b",
                quality_flag="valid",
            )
        )
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="user-2",
                timestamp=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                value=7.0,
                unit="mmol/L",
                source="sensor:a",
                quality_flag="valid",
            )
        )

        points = self.repository.list_glucose_points(
            DataScope(
                user_id="user-1",
                window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 5, 31, 0, 10, tzinfo=timezone.utc),
            )
        )
        source_points = self.repository.list_glucose_points(
            DataScope(
                user_id="user-1",
                window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 5, 31, 0, 10, tzinfo=timezone.utc),
                source="sensor:a",
            )
        )

        self.assertEqual([point.source for point in points], ["sensor:a", "sensor:b"])
        self.assertEqual(len(source_points), 1)
        self.assertEqual(source_points[0].value_mg_dl, 108.11)

    def test_device_sessions_round_trip_missing_ranges(self) -> None:
        self.repository.create_device_session(
            DeviceSession(
                session_id="sensor-session-1",
                user_id="user-1",
                device_id="device-1",
                sensor_started_at=datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc),
                warmup_ended_at=datetime(2026, 5, 30, 2, 0, tzinfo=timezone.utc),
                missing_ranges=[
                    TimeRange(
                        start=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 5, 31, 1, 15, tzinfo=timezone.utc),
                    )
                ],
            )
        )

        sessions = self.repository.list_device_sessions("user-1")

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, "sensor-session-1")
        self.assertEqual(len(sessions[0].missing_ranges), 1)

    def test_user_events_query_window_and_confirmation(self) -> None:
        self.repository.create_user_event(
            UserEvent(
                event_id="event-1",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                payload={"category": "breakfast"},
                created_by="agent",
                user_confirmed=False,
                confidence=0.7,
            )
        )
        self.repository.create_user_event(
            UserEvent(
                event_id="event-2",
                user_id="user-1",
                type="exercise",
                ts_start=datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc),
                created_by="user",
                user_confirmed=True,
            )
        )

        scope = DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )
        all_events = self.repository.list_user_events(scope)
        confirmed = self.repository.list_user_events(scope, confirmed_only=True)

        self.assertEqual([event.event_id for event in all_events], ["event-1", "event-2"])
        self.assertEqual([event.event_id for event in confirmed], ["event-2"])


if __name__ == "__main__":
    unittest.main()
