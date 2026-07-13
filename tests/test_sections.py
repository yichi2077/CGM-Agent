"""Section-level unit tests for reports/sections/* (observations, metrics, patterns)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    GlucoseEventSeverity,
    GlucoseEventType,
    WindowLabel,
)
from hermes_cgm_agent.domain.report import (
    AuthoritativeContext,
    MemoryContext,
    ReportAudience,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.sections.metrics import MetricsMixin
from hermes_cgm_agent.services.reports.sections.observations import ObservationsMixin
from hermes_cgm_agent.services.reports.sections.patterns import PatternsMixin

WINDOW_START = datetime(2026, 5, 24, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)


class _Sections(ObservationsMixin, MetricsMixin, PatternsMixin):
    """Bare mixin host — the section builders under test read no service state."""


def _scope() -> DataScope:
    return DataScope(user_id="user-1", window_start=WINDOW_START, window_end=WINDOW_END)


def _aggregate(**overrides) -> GlucoseAggregate:
    values = {
        "user_id": "user-1",
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "window_label": WindowLabel.WEEK,
        "tir": 75.0,
        "tar": 20.0,
        "tbr": 5.0,
        "gmi": 6.5,
        "cv": 25.0,
        "mbg": 130.0,
        "data_coverage": 90.0,
        "point_count": 2000,
    }
    values.update(overrides)
    return GlucoseAggregate(**values)


def _detected(event_type: GlucoseEventType, day_offset: int, event_id: str) -> GlucoseEvent:
    start = WINDOW_START + timedelta(days=day_offset, hours=14)
    return GlucoseEvent(
        event_id=event_id,
        user_id="user-1",
        event_type=event_type,
        ts_start=start,
        ts_end=start + timedelta(minutes=45),
        severity=GlucoseEventSeverity.WARNING,
        duration_minutes=45,
        point_count=9,
        summary="detected event",
        evidence_refs=[EvidenceRef(kind="event", ref_id=event_id)],
    )


class ObservationsSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sections = _Sections()

    def test_observations_section_normal(self) -> None:
        section = self.sections._observations_section(
            _scope(),
            _aggregate(),
            MemoryContext(),
            AuthoritativeContext(),
            ReportAudience.SELF,
        )
        self.assertEqual(section.section_id, "observations")
        self.assertIn("偏高", section.content)
        self.assertIn(ReportSourceTrack.FACT, section.source_tracks)
        self.assertTrue(section.evidence_refs)

    def test_observations_section_tar_zero(self) -> None:
        # Issue #6 boundary lock: TAR=0 must never yield a "偏高" narrative.
        section = self.sections._observations_section(
            _scope(),
            _aggregate(tir=100.0, tar=0.0, tbr=0.0),
            MemoryContext(),
            AuthoritativeContext(),
            ReportAudience.SELF,
        )
        self.assertNotIn("偏高", section.content)
        self.assertIn("范围里", section.content)

    def test_observations_section_empty_window(self) -> None:
        section = self.sections._observations_section(
            _scope(),
            _aggregate(tir=None, tar=None, tbr=None, point_count=0),
            MemoryContext(),
            AuthoritativeContext(),
            ReportAudience.SELF,
        )
        self.assertIn("没有留下可用数据", section.content)

    def test_observations_section_conflict_note(self) -> None:
        # G2/D031: a resolved personal-vs-authoritative conflict must surface
        # gently in the observations narrative (authoritative wins, no denial).
        memory_context = MemoryContext(
            items=[],
            conflict_resolutions=[
                {
                    "winner": "authoritative",
                    "authoritative": {"title": "TIR 目标范围"},
                    "personal": {"summary": "我的血糖通常在 12-15 mmol/L"},
                    "note": "以权威医学证据为准,温和呈现,不否定用户既往记录。",
                }
            ],
        )
        self_section = self.sections._observations_section(
            _scope(), _aggregate(), memory_context, AuthoritativeContext(), ReportAudience.SELF
        )
        self.assertIn("以参考资料为准", self_section.content)
        clinician_section = self.sections._observations_section(
            _scope(), _aggregate(), memory_context, AuthoritativeContext(), ReportAudience.CLINICIAN
        )
        self.assertIn("以权威范围为准", clinician_section.content)

    def test_observations_section_escalation_follow_up(self) -> None:
        from hermes_cgm_agent.domain import EscalationState

        section = self.sections._follow_up_section(
            _scope(),
            _aggregate(),
            [],
            ReportAudience.SELF,
            esc_state=EscalationState.CONCERN,
        )
        self.assertIn("你还好吗", section.content)


class PatternsSectionTests(unittest.TestCase):
    def test_patterns_section_weekly_repeated_events(self) -> None:
        sections = _Sections()
        detected = [
            _detected(GlucoseEventType.HYPER, 0, "e-1"),
            _detected(GlucoseEventType.HYPER, 2, "e-2"),
            _detected(GlucoseEventType.HYPER, 4, "e-3"),
        ]
        section = sections._patterns_section(
            "rep-1",
            _scope(),
            _aggregate(),
            [],
            detected,
            ReportAudience.SELF,
            timezone_name="Asia/Shanghai",
        )
        self.assertIn("3 天", section.content)
        self.assertTrue(section.g8_memory_candidates)
        self.assertFalse(section.omit_for_companion)


class MetricsSectionTests(unittest.TestCase):
    def test_metrics_section_clinician_renders_agp_values(self) -> None:
        section = _Sections()._metrics_section(
            _scope(),
            _aggregate(tbr_very_low=1.0, tar_very_high=2.0),
            ReportAudience.CLINICIAN,
        )
        self.assertIn("TIR 75.0%", section.content)
        self.assertIn("极低<54", section.content)

    def test_metrics_section_empty_window(self) -> None:
        section = _Sections()._metrics_section(
            _scope(),
            _aggregate(tir=None, tar=None, tbr=None, mbg=None, cv=None, gmi=None, point_count=0),
            ReportAudience.SELF,
        )
        self.assertIn("暂无可计算", section.content)


if __name__ == "__main__":
    unittest.main()
