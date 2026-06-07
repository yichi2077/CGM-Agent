from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import GlucosePoint, UserEvent
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import MemoryContextAssembler, SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import ReportToolService, SQLiteReportRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class E2EReportMemoryRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.memory_repository = SQLiteMemoryRepository(self.store)
        self.report_service = ReportToolService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
            memory_repository=self.memory_repository,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_daily_report_candidates_become_recallable_l1_memory(self) -> None:
        for index, value in enumerate([95, 145, 188, 126]):
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
        self.cgm_repository.create_user_event(
            UserEvent(
                event_id="evt-breakfast",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                payload={"category": "breakfast"},
                confidence=1.0,
                created_by="user",
                user_confirmed=True,
            )
        )

        result = self.report_service.generate(
            {
                "user_id": "user-1",
                "report_type": "daily",
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            }
        )
        recall = MemoryContextAssembler(repository=self.memory_repository).build_memory_context(
            user_id="user-1",
            query="breakfast meal event",
            top_k=3,
        )

        self.assertEqual(result.memory_ingest["enqueued"], 1)
        self.assertEqual(result.memory_ingest["auto_accepted"], 1)
        self.assertEqual(len(self.memory_repository.list_episodes("user-1")), 1)
        self.assertGreater(len(recall.items), 0)
        joined = " ".join(str(item["summary"]).lower() for item in recall.items)
        self.assertIn("meal", joined)


if __name__ == "__main__":
    unittest.main()
