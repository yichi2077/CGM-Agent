"""Tests for StreamMemoryService data_gap filtering (A4).

data_gap events are operational metadata, not clinical episodes.  They must
not be promoted to L1 episodes in the memory stream (parity with derive.py).
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain.cgm import (
    EvidenceRef,
    GlucoseEvent,
    GlucoseEventSeverity,
    GlucoseEventType,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.stream import StreamMemoryService
from hermes_cgm_agent.storage.sqlite import SQLiteStore

UTC = timezone.utc


def _make_event(
    event_id: str,
    event_type: GlucoseEventType,
    *,
    user_id: str = "u1",
    start: datetime | None = None,
) -> GlucoseEvent:
    start = start or datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
    return GlucoseEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        ts_start=start,
        ts_end=start + timedelta(minutes=15),
        severity=GlucoseEventSeverity.WARNING,
        nadir_value_mg_dl=50.0 if event_type == GlucoseEventType.HYPO else None,
        peak_value_mg_dl=260.0 if event_type == GlucoseEventType.HYPER else None,
        duration_minutes=15.0,
        point_count=3,
        summary=f"test {event_type.value} event",
        evidence_refs=[
            EvidenceRef(
                kind="glucose_point",
                ref_id=f"glucose:{user_id}:{start.isoformat()}",
                summary=f"{start.isoformat()} 50 mg/dL",
            ),
        ],
    )


class DataGapFilterTests(unittest.TestCase):
    """A4: data_gap events must not be promoted to L1 episodes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "stream.db"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.memory_repository = SQLiteMemoryRepository(self.store)
        self.service = StreamMemoryService(
            cgm_repository=self.cgm_repository,
            memory_repository=self.memory_repository,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_data_gap_not_ingested_to_l1(self) -> None:
        """A data_gap event should not create an L1 episode."""
        now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
        event = _make_event("evt-gap-1", GlucoseEventType.DATA_GAP, start=now)
        self.service.ingest_poll_result(
            user_id="u1",
            source="xdrip",
            reading_times=[now],
            inserted_point_count=2,
            inserted_events=[event],
            now=now,
        )
        episodes = self.memory_repository.list_episodes("u1")
        self.assertEqual(len(episodes), 0)

    def test_hypo_event_ingested_to_l1(self) -> None:
        """A non-data_gap event (hypo) should still create an L1 episode."""
        now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
        event = _make_event("evt-hypo-1", GlucoseEventType.HYPO, start=now)
        self.service.ingest_poll_result(
            user_id="u1",
            source="xdrip",
            reading_times=[now],
            inserted_point_count=2,
            inserted_events=[event],
            now=now,
        )
        episodes = self.memory_repository.list_episodes("u1")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].episode_type, GlucoseEventType.HYPO.value)

    def test_mixed_events_only_non_data_gap_ingested(self) -> None:
        """When data_gap and hypo events are both present, only hypo creates L1."""
        now = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)
        gap_event = _make_event("evt-gap-2", GlucoseEventType.DATA_GAP, start=now)
        hypo_event = _make_event("evt-hypo-2", GlucoseEventType.HYPO, start=now)
        self.service.ingest_poll_result(
            user_id="u1",
            source="xdrip",
            reading_times=[now],
            inserted_point_count=4,
            inserted_events=[gap_event, hypo_event],
            now=now,
        )
        episodes = self.memory_repository.list_episodes("u1")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].episode_type, GlucoseEventType.HYPO.value)


if __name__ == "__main__":
    unittest.main()
