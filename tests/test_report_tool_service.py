from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import (
    ReportToolService,
    SQLiteReportRepository,
    auto_ingest_memory_enabled,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class ReportToolServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.memory_repository = SQLiteMemoryRepository(self.store)
        self.service = ReportToolService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
            memory_repository=self.memory_repository,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_auto_ingest_defaults_to_enabled_except_doctor_reports(self) -> None:
        self.assertTrue(auto_ingest_memory_enabled({"report_type": "weekly"}))
        self.assertTrue(auto_ingest_memory_enabled({"report_type": "daily"}))
        self.assertFalse(auto_ingest_memory_enabled({"report_type": "doctor"}))

    def test_auto_ingest_rejects_string_boolean(self) -> None:
        with self.assertRaisesRegex(ValueError, "auto_ingest_memory must be a boolean"):
            auto_ingest_memory_enabled(
                {"report_type": "weekly", "auto_ingest_memory": "false"}
            )

    def test_generate_enqueues_weekly_memory_candidates_by_default(self) -> None:
        self._create_points([190, 195, 200, 205])

        result = self.service.generate(
            {
                "user_id": "user-1",
                "report_type": "weekly",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            }
        )

        self.assertEqual(result.report.report_type, "weekly")
        self.assertGreater(result.memory_ingest["enqueued"], 0)
        self.assertEqual(
            len(self.memory_repository.list_candidates("user-1")),
            result.memory_ingest["enqueued"],
        )

    def test_generate_does_not_auto_ingest_doctor_reports_by_default(self) -> None:
        self._create_points([190, 195, 200, 205])

        result = self.service.generate(
            {
                "user_id": "user-1",
                "report_type": "doctor",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-18T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            }
        )

        self.assertFalse(result.memory_ingest["enabled"])
        self.assertEqual(self.memory_repository.list_candidates("user-1"), [])

    def test_generate_rejects_string_retrieve_context_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "retrieve_context must be a boolean"):
            self.service.generate(
                {
                    "user_id": "user-1",
                    "report_type": "daily",
                    "retrieve_context": "false",
                    "data_scope": {
                        "user_id": "user-1",
                        "window_start": "2026-05-31T00:00:00+00:00",
                        "window_end": "2026-06-01T00:00:00+00:00",
                    },
                }
            )

    def _create_points(self, values: list[int]) -> None:
        for index, value in enumerate(values):
            self.cgm_repository.create_glucose_point(
                GlucosePoint(
                    user_id="user-1",
                    timestamp=datetime(2026, 5, 31, index, 0, tzinfo=timezone.utc),
                    value=value,
                    unit="mg/dL",
                    source="sensor:test",
                    quality_flag="valid",
                )
            )


if __name__ == "__main__":
    unittest.main()
