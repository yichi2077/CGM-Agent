from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import DataScope, GlucosePoint
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class TimezoneCorrectnessTests(unittest.TestCase):
    """Regression tests for C1 (importer forced naive->UTC) and C6 (query
    bounds compared lexicographically without UTC normalization)."""

    # ---- C1: import -> normalize honours the configured source timezone ----

    def test_naive_csv_timestamps_use_configured_timezone_end_to_end(self) -> None:
        csv_text = (
            "timestamp,value,unit\n"
            "2026-05-31 08:00:00,100,mg/dL\n"
            "2026-05-31 08:05:00,102,mg/dL\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "naive_local.csv"
            path.write_text(csv_text, encoding="utf-8")
            batch = CGMImporter().import_file(path)

        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(
                user_id="user-1", source="sensor:test", default_timezone="Asia/Shanghai"
            ),
        )

        self.assertEqual(len(result.points), 2)
        # 08:00 Asia/Shanghai (UTC+8) must be stored as 00:00 UTC, NOT 08:00 UTC.
        self.assertEqual(
            result.points[0].timestamp.isoformat(), "2026-05-31T00:00:00+00:00"
        )

    def test_offset_aware_csv_timestamps_are_preserved_as_instant(self) -> None:
        csv_text = (
            "timestamp,value,unit\n"
            "2026-05-31T08:00:00+09:00,100,mg/dL\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "aware.csv"
            path.write_text(csv_text, encoding="utf-8")
            batch = CGMImporter().import_file(path)

        # default_timezone must NOT override an explicit offset.
        result = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(
                user_id="user-1", source="sensor:test", default_timezone="Asia/Shanghai"
            ),
        )
        self.assertEqual(
            result.points[0].timestamp.isoformat(), "2026-05-30T23:00:00+00:00"
        )

    # ---- C6: range queries normalize bounds to UTC before comparison ----

    def _repo(self, temp_dir: str) -> SQLiteCGMRepository:
        store = SQLiteStore(Path(temp_dir) / "app.db")
        store.initialize()
        return SQLiteCGMRepository(store)

    def test_naive_stored_fact_and_naive_bounds_are_consistent(self) -> None:
        # C6 residual: a fact stored with a NAIVE timestamp and a naive query
        # window must compare consistently (both canonicalized to UTC), so a row
        # exactly at window_start is not dropped by lexicographic TEXT compare.
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, 0, 0),  # naive
                    value=100,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )
            scope = DataScope(
                user_id="user-1",
                window_start=datetime(2026, 5, 31, 0, 0),  # naive, == point time
                window_end=datetime(2026, 5, 31, 6, 0),
            )
            points = repo.list_glucose_points(scope)

        self.assertEqual(len(points), 1)

    def test_query_with_nonutc_offset_bounds_does_not_drop_in_window_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            repo.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                    value=100,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )
            # Window expressed in +08:00 that contains 2026-05-31T00:00Z
            # (07:00+08:00 == 2026-05-30T23:00Z .. 09:00+08:00 == 2026-05-31T01:00Z)
            tz_shanghai = timezone(timedelta(hours=8))
            scope = DataScope(
                user_id="user-1",
                window_start=datetime(2026, 5, 31, 7, 0, tzinfo=tz_shanghai),
                window_end=datetime(2026, 5, 31, 9, 0, tzinfo=tz_shanghai),
            )
            points = repo.list_glucose_points(scope)

        self.assertEqual(len(points), 1)


if __name__ == "__main__":
    unittest.main()
