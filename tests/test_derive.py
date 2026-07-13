"""Unit tests for services.memory.derive (episodes_from_detected_events)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import (
    EvidenceRef,
    GlucoseEvent,
    GlucoseEventSeverity,
    GlucoseEventType,
)
from hermes_cgm_agent.services.memory.derive import episodes_from_detected_events

NOW = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


def _event(
    event_type: GlucoseEventType,
    event_id: str = "e-1",
    nadir: float | None = None,
    peak: float | None = None,
) -> GlucoseEvent:
    start = NOW - timedelta(hours=2)
    return GlucoseEvent(
        event_id=event_id,
        user_id="user-1",
        event_type=event_type,
        ts_start=start,
        ts_end=start + timedelta(minutes=30),
        severity=GlucoseEventSeverity.WARNING,
        nadir_value_mg_dl=nadir,
        peak_value_mg_dl=peak,
        duration_minutes=30,
        point_count=6,
        summary="hypo event below 70 mg/dL",
        evidence_refs=[EvidenceRef(kind="event", ref_id=event_id)],
    )


class DeriveEpisodesTests(unittest.TestCase):
    def test_hypo_event_becomes_episode_with_companion_summary(self) -> None:
        episodes = episodes_from_detected_events(
            [_event(GlucoseEventType.HYPO, nadir=62)], now=NOW
        )
        self.assertEqual(len(episodes), 1)
        ep = episodes[0]
        self.assertEqual(ep.episode_id, "evt-e-1")
        self.assertEqual(ep.user_id, "user-1")
        self.assertEqual(ep.episode_type, "hypo")
        self.assertEqual(ep.created_at, NOW)
        # D058: the recalled summary is companion Chinese life-language,
        # not the detector's raw English clinical string.
        self.assertNotEqual(ep.summary, "hypo event below 70 mg/dL")
        self.assertTrue(ep.summary)

    def test_data_gap_events_are_excluded(self) -> None:
        # D052: a sensor outage is a data-quality fact, not a behavior pattern.
        events = [
            _event(GlucoseEventType.DATA_GAP, event_id="gap-1"),
            _event(GlucoseEventType.HYPER, event_id="hyper-1", peak=260),
        ]
        episodes = episodes_from_detected_events(events, now=NOW)
        self.assertEqual([e.episode_type for e in episodes], ["hyper"])

    def test_empty_events_yield_no_episodes(self) -> None:
        self.assertEqual(episodes_from_detected_events([], now=NOW), [])

    def test_evidence_refs_carried_over(self) -> None:
        episodes = episodes_from_detected_events(
            [_event(GlucoseEventType.RAPID_RISE, peak=210)], now=NOW
        )
        self.assertEqual(episodes[0].evidence_refs[0].ref_id, "e-1")
        self.assertEqual(episodes[0].confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
