from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import RawCGMRecord, RawImportBatch
from hermes_cgm_agent.services.data import (
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class CGMNormalizerTests(unittest.TestCase):
    def test_normalizes_raw_records_into_glucose_points(self) -> None:
        batch = RawImportBatch(
            batch_id="batch-1",
            source_name="unit-test",
            source_format="csv",
            records=[
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "r1"},
                    row_number=1,
                    recorded_at=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                    value=6.0,
                    unit="mmol/L",
                    device_id="device-1",
                    source_record_id="r1",
                )
            ],
        )

        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(user_id="user-1", source="sensor:test"),
        )

        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.points[0].user_id, "user-1")
        self.assertEqual(result.points[0].source, "sensor:test")
        self.assertEqual(result.points[0].value_mg_dl, 108.11)
        self.assertEqual(result.points[0].quality_flag, "valid")
        self.assertEqual(result.issues, [])

    def test_applies_timezone_warmup_suspect_duplicate_and_gap_rules(self) -> None:
        batch = RawImportBatch(
            batch_id="batch-2",
            source_name="unit-test",
            source_format="csv",
            records=[
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "warmup"},
                    row_number=1,
                    recorded_at=datetime(2026, 5, 31, 8, 0),
                    value=100,
                    unit="mg/dL",
                    source_record_id="warmup",
                ),
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "duplicate"},
                    row_number=2,
                    recorded_at=datetime(2026, 5, 31, 8, 0),
                    value=101,
                    unit="mg/dL",
                    source_record_id="duplicate",
                ),
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "suspect"},
                    row_number=3,
                    recorded_at=datetime(2026, 5, 31, 8, 20),
                    value=450,
                    unit="mg/dL",
                    source_record_id="suspect",
                ),
            ],
        )

        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(
                user_id="user-1",
                source="sensor:test",
                default_timezone="Asia/Shanghai",
                warmup_until=datetime(2026, 5, 31, 8, 10),
                gap_threshold_minutes=10,
                expected_interval_minutes=5,
            ),
        )

        self.assertEqual(len(result.points), 2)
        self.assertEqual(result.points[0].timestamp.isoformat(), "2026-05-31T00:00:00+00:00")
        self.assertEqual(result.points[0].quality_flag, "warmup")
        self.assertEqual(result.points[1].quality_flag, "suspect")
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(len(result.missing_ranges), 1)
        self.assertEqual(result.missing_ranges[0].start.isoformat(), "2026-05-31T00:05:00+00:00")
        self.assertEqual(result.missing_ranges[0].end.isoformat(), "2026-05-31T00:20:00+00:00")
        self.assertEqual(result.issues[0].field, "timestamp")

    def test_missing_required_parsed_fields_become_issues(self) -> None:
        batch = RawImportBatch(
            batch_id="batch-3",
            source_name="unit-test",
            source_format="csv",
            records=[
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "missing"},
                    row_number=1,
                )
            ],
        )

        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(user_id="user-1", source="sensor:test"),
        )

        self.assertEqual(result.points, [])
        self.assertEqual(
            [(issue.field, issue.message) for issue in result.issues],
            [
                ("recorded_at", "Missing recorded_at"),
                ("value", "Missing glucose value"),
                ("unit", "Missing glucose unit"),
            ],
        )

    def test_normalized_points_can_persist_to_repository(self) -> None:
        batch = RawImportBatch(
            batch_id="batch-4",
            source_name="unit-test",
            source_format="csv",
            records=[
                RawCGMRecord(
                    source_id="source",
                    source_format="csv",
                    raw_payload={"id": "r1"},
                    row_number=1,
                    recorded_at=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                    value=100,
                    unit="mg/dL",
                )
            ],
        )
        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(user_id="user-1", source="sensor:test"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repository = SQLiteCGMRepository(store)

            for point in result.points:
                repository.create_glucose_point(point)
            status = repository.status()

        self.assertEqual(status.glucose_point_count, 1)


if __name__ == "__main__":
    unittest.main()
