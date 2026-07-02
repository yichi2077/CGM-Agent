from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hermes_cgm_agent.domain import DataScope, GlucoseEventType
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.sources import SourcePollConfig, SourcePollService
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class FakeHTTPClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, int]] = []

    def fetch_json(self, *, url: str, kind: str, count: int) -> tuple[str, Any]:
        self.calls.append((url, kind, count))
        return "http://127.0.0.1:17580/sgv.json?count=2", self.payload


class SourcePollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_poll_inserts_dedupes_persists_gap_and_handoff_tools_can_read(self) -> None:
        start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        later = start + timedelta(minutes=45)
        payload = [
            {"_id": "r1", "sgv": 100, "date": _epoch_ms(start), "direction": "Flat"},
            {"_id": "r2", "sgv": 111, "date": _epoch_ms(later), "direction": "SingleUp"},
        ]
        client = FakeHTTPClient(payload)
        service = SourcePollService(repository=self.repository, client=client)

        first = service.poll(
            user_id="user-1",
            kind="xdrip",
            url="http://127.0.0.1:17580",
            count=2,
            received_at=later + timedelta(minutes=1),
        )
        second = service.poll(
            user_id="user-1",
            kind="xdrip",
            url="http://127.0.0.1:17580",
            count=2,
            received_at=later + timedelta(minutes=2),
        )
        scope = DataScope(
            user_id="user-1",
            window_start=start - timedelta(minutes=1),
            window_end=later + timedelta(minutes=10),
            source=first.source,
        )
        points = self.repository.list_glucose_points(scope)
        events = self.repository.list_glucose_events(scope)
        timeseries = self.executor.execute(
            tool_name="timeseries.get_points",
            arguments={
                "data_scope": scope.model_dump(mode="json"),
                "limit": 10,
            },
            session_id="source-poll",
        ).to_dict()
        l0 = self.executor.execute(
            tool_name="context.get_l0",
            arguments={
                "user_id": "user-1",
                "source": first.source,
                "anchor_at": (later + timedelta(minutes=5)).isoformat(),
            },
            session_id="source-poll",
        ).to_dict()

        self.assertEqual(client.calls, [("http://127.0.0.1:17580", "xdrip", 2)] * 2)
        self.assertEqual(first.parsed_count, 2)
        self.assertEqual(first.inserted_count, 2)
        self.assertEqual(first.duplicate_count, 0)
        self.assertEqual(first.issue_count, 0)
        self.assertEqual(first.detected_event_inserted, 1)
        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(second.duplicate_count, 2)
        self.assertEqual(second.detected_event_duplicate, 1)
        self.assertEqual([point.value_mg_dl for point in points], [100.0, 111.0])
        self.assertEqual(points[0].received_at, later + timedelta(minutes=1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, GlucoseEventType.DATA_GAP)
        self.assertEqual(timeseries["status"], "ok")
        self.assertEqual(len(timeseries["points"]), 2)
        self.assertEqual(l0["status"], "ok")
        self.assertEqual(len(l0["context"]["high_res_recent"]), 2)
        self.assertEqual(self.repository.status().import_batch_count, 2)

    def test_expected_interval_config_flows_into_gap_detection(self) -> None:
        start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        later = start + timedelta(minutes=5)
        payload = [
            {"_id": "r1", "sgv": 100, "date": _epoch_ms(start), "direction": "Flat"},
            {"_id": "r2", "sgv": 101, "date": _epoch_ms(later), "direction": "Flat"},
        ]
        service = SourcePollService(
            repository=self.repository,
            client=FakeHTTPClient(payload),
            config=SourcePollConfig(expected_interval_minutes=1),
        )

        result = service.poll(
            user_id="user-1",
            kind="xdrip",
            url="http://127.0.0.1:17580",
            count=2,
            received_at=later + timedelta(minutes=1),
        )
        scope = DataScope(
            user_id="user-1",
            window_start=start - timedelta(minutes=1),
            window_end=later + timedelta(minutes=2),
            source=result.source,
        )
        events = self.repository.list_glucose_events(scope)

        self.assertEqual(result.detected_event_inserted, 1)
        self.assertEqual(events[0].event_type, GlucoseEventType.DATA_GAP)


if __name__ == "__main__":
    unittest.main()
