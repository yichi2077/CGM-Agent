"""Tests for the 2026-07-16 MVP-audit remediation batch.

Covers:
- P0-1: direction-split red-zone first-aid templates + yellow-low 15-15 prefix
- P0-2: single overnight low forces the next-morning daily push
- P1-4: silent consent never auto-advances medical/safety hypotheses
- P1-5: deterministic affect detection + emotional-first orchestration
- P1-6: push_tick closes the staged consolidation loop (idempotent per day)
- P1-7: ingestion hard range gate + router WARMUP exclusion / SUSPECT handling
- P2-11: vulnerable-population day-3 reinforced check-in
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    DataScope,
    GlucosePoint,
    HypothesisCategory,
    HypothesisState,
    L3Hypothesis,
    QualityFlag,
    RawCGMRecord,
    RawImportBatch,
)
from hermes_cgm_agent.domain.cgm import SourceFormat
from hermes_cgm_agent.domain.memory import EscalationState
from hermes_cgm_agent.services.data.normalizer import (
    CGMNormalizer,
    NormalizationConfig,
)
from hermes_cgm_agent.services.memory.affect import detect_affect, is_affect_hit
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.services.safety.router import (
    RED_ZONE_HIGH_TEMPLATE,
    RED_ZONE_LOW_TEMPLATE,
    RED_ZONE_TEMPLATE,
    SafetyRouter,
    YELLOW_ZONE_LOW_TEMPLATE,
)
from hermes_cgm_agent.services.scheduling.scheduler import (
    PushSchedulerConfig,
    PushSchedulerService,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

UTC = timezone.utc


def _point(
    value: float,
    *,
    ts: datetime,
    quality_flag: str = "valid",
    user_id: str = "u1",
) -> GlucosePoint:
    return GlucosePoint(
        user_id=user_id,
        timestamp=ts,
        value=value,
        unit="mg/dL",
        source="sensor:test",
        quality_flag=quality_flag,
    )


def _scope(start: datetime, end: datetime, user_id: str = "u1") -> DataScope:
    return DataScope(user_id=user_id, window_start=start, window_end=end)


def _sustained(values: list[float], *, start: datetime, quality_flag: str = "valid"):
    """Three+ points 5 minutes apart -> passes the 10-min sustained gate."""
    return [
        _point(v, ts=start + timedelta(minutes=5 * i), quality_flag=quality_flag)
        for i, v in enumerate(values)
    ]


class RedZoneTemplateSplitTests(unittest.TestCase):
    """P0-1: red-zone messages carry direction-specific first-aid guidance."""

    def setUp(self) -> None:
        self.router = SafetyRouter()
        self.start = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
        self.scope = _scope(self.start, self.start + timedelta(hours=1))

    def test_low_red_zone_gets_severe_hypo_guidance(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([45, 44, 43], start=self.start),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertIn("你的血糖很低", decision.message)
        self.assertIn("胰高血糖素", decision.message)
        self.assertIn("15 克速效碳水", decision.message)
        # "先救命再 defer": the defer close survives at the tail.
        self.assertTrue(decision.message.endswith(RED_ZONE_TEMPLATE))
        self.assertEqual(decision.safety_result["rep_direction"], "极低")

    def test_high_red_zone_gets_ketone_hydration_guidance(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([290, 300, 310], start=self.start),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertIn("你的血糖很高", decision.message)
        self.assertIn("酮体", decision.message)
        self.assertTrue(decision.message.endswith(RED_ZONE_TEMPLATE))
        self.assertEqual(decision.safety_result["rep_direction"], "极高")

    def test_mixed_red_zone_prioritizes_low(self) -> None:
        # Hypoglycemia is the more acute risk when both directions appear.
        points = _sustained([45, 44, 43], start=self.start) + _sustained(
            [290, 300, 310], start=self.start + timedelta(minutes=30)
        )
        decision = self.router.evaluate(
            scope=self.scope, points=points, now=self.start + timedelta(hours=1)
        )
        assert decision.message is not None
        self.assertIn("你的血糖很低", decision.message)

    def test_yellow_low_carries_15_15_rule(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([62, 63, 64], start=self.start),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertIn("15 克速效碳水", decision.message)
        self.assertIn("15 分钟后复测", decision.message)

    def test_yellow_high_keeps_generic_prefix(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([200, 210, 220], start=self.start),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertEqual(decision.safety_result["status"], "yellow_zone")
        self.assertNotIn("速效碳水", decision.message)

    def test_templates_avoid_companion_blacklist(self) -> None:
        from hermes_cgm_agent.services.reports.narrative_templates import (
            check_companion_text,
        )

        for template in (
            RED_ZONE_LOW_TEMPLATE.format(value=45.0),
            RED_ZONE_HIGH_TEMPLATE.format(value=300.0),
            YELLOW_ZONE_LOW_TEMPLATE.format(value=62.0),
        ):
            violations = [
                v for v in check_companion_text(template, max_len=10_000)
                if not v.startswith("length:")
            ]
            self.assertEqual(violations, [], f"blacklist hit in: {template}")


class SuspectAndWarmupRoutingTests(unittest.TestCase):
    """P1-7: WARMUP excluded; SUSPECT keeps red sensitivity + fingerstick note."""

    def setUp(self) -> None:
        self.router = SafetyRouter()
        self.start = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)
        self.scope = _scope(self.start, self.start + timedelta(hours=1))

    def test_warmup_points_never_trigger_zones(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([45, 44, 43], start=self.start, quality_flag="warmup"),
            now=self.start + timedelta(hours=1),
        )
        self.assertEqual(decision.safety_result["status"], "clear")

    def test_suspect_only_red_still_fires_with_fingerstick_note(self) -> None:
        # A real 35 mg/dL severe low is SUSPECT-flagged by the normalizer —
        # the router must keep alerting on it (sensitivity first).
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([35, 34, 33], start=self.start, quality_flag="suspect"),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertEqual(decision.safety_result["status"], "red_zone")
        self.assertTrue(decision.safety_result["suspect_only"])
        self.assertIn("指尖血", decision.message)

    def test_valid_red_has_no_fingerstick_note(self) -> None:
        decision = self.router.evaluate(
            scope=self.scope,
            points=_sustained([45, 44, 43], start=self.start),
            now=self.start + timedelta(hours=1),
        )
        assert decision.message is not None
        self.assertFalse(decision.safety_result["suspect_only"])
        self.assertNotIn("指尖血", decision.message)


class NormalizerRangeGateTests(unittest.TestCase):
    """P1-7: physiologically impossible readings are rejected at ingestion."""

    def _normalize(self, value: float, unit: str = "mg/dL"):
        record = RawCGMRecord(
            source_id="r1",
            source_format=SourceFormat.CSV,
            raw_payload={},
            row_number=1,
            recorded_at=datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
            value=value,
            unit=unit,
        )
        batch = RawImportBatch(
            batch_id="b1",
            source_name="test",
            source_format=SourceFormat.CSV,
            records=[record],
        )
        config = NormalizationConfig(user_id="u1", source="test")
        return CGMNormalizer().normalize_batch(batch, config)

    def test_unit_bug_value_is_rejected(self) -> None:
        # 5.4 mmol/L mistakenly written as mg/dL -> below the 20 mg/dL floor.
        result = self._normalize(5.4)
        self.assertEqual(result.points, [])
        self.assertEqual(len(result.issues), 1)
        self.assertIn("plausible range", result.issues[0].message)

    def test_absurd_high_value_is_rejected(self) -> None:
        result = self._normalize(900)
        self.assertEqual(result.points, [])
        self.assertEqual(len(result.issues), 1)

    def test_extreme_but_plausible_low_is_kept_as_suspect(self) -> None:
        # 35 mg/dL: plausible severe hypoglycemia — stored, flagged SUSPECT.
        result = self._normalize(35)
        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.points[0].quality_flag, QualityFlag.SUSPECT)

    def test_normal_value_is_valid(self) -> None:
        result = self._normalize(110)
        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.points[0].quality_flag, QualityFlag.VALID)


class _SchedulerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self._tmp.name) / "test.db")
        self.store.initialize()
        self.memory = SQLiteMemoryRepository(self.store)
        self.scheduler = PushSchedulerService(
            store=self.store,
            config=PushSchedulerConfig(timezone="Asia/Shanghai", silence_days=3),
        )
        self.addCleanup(self._tmp.cleanup)

    def _seed_hypothesis(
        self,
        *,
        hypothesis_id: str,
        category: HypothesisCategory,
        last_checked: datetime,
    ) -> None:
        self.memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=hypothesis_id,
                user_id="u1",
                statement="午后散步后血糖更平稳",
                state=HypothesisState.CANDIDATE,
                category=category,
                last_checked=last_checked,
                created_at=last_checked,
                updated_at=last_checked,
            )
        )


class SilentConsentCategoryFilterTests(_SchedulerFixture):
    """P1-4: silent consent only ever advances behavioral candidates."""

    def test_behavioral_candidate_advances(self) -> None:
        now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        stale = now - timedelta(days=5)
        self._seed_hypothesis(
            hypothesis_id="h-behavioral",
            category=HypothesisCategory.BEHAVIORAL,
            last_checked=stale,
        )
        advanced = self.scheduler.apply_silent_consent(user_id="u1", now=now)
        self.assertEqual([a["hypothesis_id"] for a in advanced], ["h-behavioral"])

    def test_medical_and_safety_candidates_never_advance(self) -> None:
        now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        stale = now - timedelta(days=30)
        self._seed_hypothesis(
            hypothesis_id="h-medical",
            category=HypothesisCategory.MEDICAL,
            last_checked=stale,
        )
        self._seed_hypothesis(
            hypothesis_id="h-safety",
            category=HypothesisCategory.SAFETY,
            last_checked=stale,
        )
        advanced = self.scheduler.apply_silent_consent(user_id="u1", now=now)
        self.assertEqual(advanced, [])
        states = {
            h.hypothesis_id: h.state for h in self.memory.list_hypotheses("u1")
        }
        self.assertEqual(states["h-medical"], HypothesisState.CANDIDATE)
        self.assertEqual(states["h-safety"], HypothesisState.CANDIDATE)

    def test_category_round_trips_through_storage(self) -> None:
        now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
        self._seed_hypothesis(
            hypothesis_id="h-medical",
            category=HypothesisCategory.MEDICAL,
            last_checked=now,
        )
        loaded = self.memory.list_hypotheses("u1")[0]
        self.assertEqual(loaded.category, HypothesisCategory.MEDICAL)


class OvernightLowPushTests(_SchedulerFixture):
    """P0-2: a single overnight low forces the next-morning daily push."""

    def _seed_overnight_low(self, *, day: datetime) -> None:
        # Sustained 03:00-04:00 local low (Asia/Shanghai = UTC+8 -> 19:00 UTC
        # previous day). Long enough for the hypo event detector.
        start_local = day.replace(hour=3, minute=0)
        start = (start_local - timedelta(hours=8)).replace(tzinfo=UTC)  # to UTC
        for i in range(13):  # 60 minutes of 5-min samples at 58 mg/dL
            self.scheduler.cgm.create_glucose_point(
                _point(58, ts=start + timedelta(minutes=5 * i))
            )
        # Add normal daytime points so aggregates exist.
        for i in range(6):
            self.scheduler.cgm.create_glucose_point(
                _point(110, ts=start + timedelta(hours=2, minutes=5 * i))
            )

    def test_overnight_low_detected_for_today(self) -> None:
        day = datetime(2026, 7, 16, 0, 0)
        self._seed_overnight_low(day=day)
        now = datetime(2026, 7, 16, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        self.assertTrue(
            self.scheduler._overnight_low_today("u1", now.astimezone(UTC))
        )

    def test_overnight_low_forces_daily_push_with_gentle_text(self) -> None:
        day = datetime(2026, 7, 16, 0, 0)
        self._seed_overnight_low(day=day)
        now = datetime(2026, 7, 16, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        result = self.scheduler.push_tick(user_id="u1", now=now.astimezone(UTC))
        daily = [p for p in result.pushed if p["tier"] == "daily"]
        self.assertEqual(len(daily), 1)
        self.assertIn("昨晚", daily[0]["content"])

    def test_no_overnight_low_keeps_trend_gate(self) -> None:
        # Only normal daytime points: the trend gate must stay closed.
        start = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)
        for i in range(6):
            self.scheduler.cgm.create_glucose_point(
                _point(110, ts=start + timedelta(minutes=5 * i))
            )
        now = datetime(2026, 7, 16, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        result = self.scheduler.push_tick(user_id="u1", now=now.astimezone(UTC))
        self.assertEqual([p for p in result.pushed if p["tier"] == "daily"], [])


class ConsolidationTickTests(_SchedulerFixture):
    """P1-6: push_tick runs staged consolidation once per local day."""

    def test_first_tick_consolidates_second_tick_skips(self) -> None:
        now = datetime(2026, 7, 16, 1, 30, tzinfo=UTC)  # 09:30 local
        first = self.scheduler.push_tick(user_id="u1", now=now)
        self.assertTrue(first.consolidated)
        second = self.scheduler.push_tick(
            user_id="u1", now=now + timedelta(minutes=30)
        )
        self.assertFalse(second.consolidated)

    def test_next_day_consolidates_again(self) -> None:
        now = datetime(2026, 7, 16, 1, 30, tzinfo=UTC)
        self.scheduler.push_tick(user_id="u1", now=now)
        tomorrow = self.scheduler.push_tick(
            user_id="u1", now=now + timedelta(days=1)
        )
        self.assertTrue(tomorrow.consolidated)

    def test_to_dict_carries_consolidated_flag(self) -> None:
        now = datetime(2026, 7, 16, 1, 30, tzinfo=UTC)
        body = self.scheduler.push_tick(user_id="u1", now=now).to_dict()
        self.assertIn("consolidated", body)


class AffectDetectionTests(unittest.TestCase):
    """P1-5: deterministic affect detection."""

    def test_distress_text_hits(self) -> None:
        self.assertTrue(is_affect_hit("今天真的好烦，血糖怎么都压不下去"))
        self.assertTrue(is_affect_hit("我最近压力大，心情不好"))
        self.assertTrue(is_affect_hit("I feel so anxious about my numbers"))

    def test_neutral_text_misses(self) -> None:
        self.assertEqual(detect_affect("帮我看看这周的血糖报告"), [])
        self.assertEqual(detect_affect(None), [])
        self.assertEqual(detect_affect(""), [])

    def test_matched_terms_returned(self) -> None:
        self.assertIn("焦虑", detect_affect("有点焦虑"))


class AffectReportOrchestrationTests(unittest.TestCase):
    """P1-5: a distressed user_message makes the report lead with empathy."""

    def setUp(self) -> None:
        from hermes_cgm_agent.services.data import SQLiteCGMRepository
        from hermes_cgm_agent.services.reports import (
            ReportService,
            SQLiteReportRepository,
        )

        self._tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self._tmp.name) / "test.db")
        self.store.initialize()
        self.cgm = SQLiteCGMRepository(self.store)
        self.reports = ReportService(
            cgm_repository=self.cgm,
            report_repository=SQLiteReportRepository(self.store),
        )
        start = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)
        for i in range(12):
            self.cgm.create_glucose_point(
                _point(115, ts=start + timedelta(minutes=10 * i), user_id="user-1")
            )
        self.addCleanup(self._tmp.cleanup)

    def _generate(self, user_message: str | None):
        from hermes_cgm_agent.domain.report import ReportInput

        return self.reports.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                anchor_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                user_message=user_message,
            )
        )

    def test_distress_message_prepends_empathy_section(self) -> None:
        report = self._generate("今天太难受了，看到数字就想哭")
        self.assertEqual(report.sections[0].section_id, "affect_ack")
        self.assertIn("辛苦", report.sections[0].content)

    def test_neutral_message_has_no_empathy_section(self) -> None:
        report = self._generate("帮我生成今天的报告")
        self.assertNotIn(
            "affect_ack", [section.section_id for section in report.sections]
        )

    def test_no_message_has_no_empathy_section(self) -> None:
        report = self._generate(None)
        self.assertNotIn(
            "affect_ack", [section.section_id for section in report.sections]
        )


class VulnerableDay3Tests(unittest.TestCase):
    """P2-11: vulnerable ladder is day 1 / day 3 / day 5."""

    def test_state_ladder_unchanged(self) -> None:
        self.assertEqual(
            EscalationState.derive(1, is_vulnerable=True), EscalationState.CONCERN
        )
        self.assertEqual(
            EscalationState.derive(3, is_vulnerable=True), EscalationState.CONCERN
        )
        self.assertEqual(
            EscalationState.derive(5, is_vulnerable=True),
            EscalationState.EXTERNAL_SUPPORT,
        )

    def test_day3_reinforced_wording(self) -> None:
        from hermes_cgm_agent.domain import GlucoseAggregate
        from hermes_cgm_agent.domain.report import ReportAudience
        from hermes_cgm_agent.services.reports.builder import ReportService

        service = ReportService.__new__(ReportService)  # sections mixin only
        scope = _scope(
            datetime(2026, 7, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
        )
        aggregate = GlucoseAggregate(
            user_id="u1",
            window_start=scope.window_start,
            window_end=scope.window_end,
            point_count=100,
            data_coverage=90.0,
            tir=55.0,
            tar=30.0,
            tbr=15.0,
            mbg=150.0,
        )
        day3 = service._follow_up_section(
            scope,
            aggregate,
            [],
            ReportAudience.SELF,
            esc_state=EscalationState.CONCERN,
            consecutive_days=3,
            is_vulnerable=True,
        )
        self.assertIn("第三天", day3.content)
        day1 = service._follow_up_section(
            scope,
            aggregate,
            [],
            ReportAudience.SELF,
            esc_state=EscalationState.CONCERN,
            consecutive_days=1,
            is_vulnerable=True,
        )
        self.assertNotIn("第三天", day1.content)
        # Standard (non-vulnerable) users keep the original day-3 wording.
        standard_day3 = service._follow_up_section(
            scope,
            aggregate,
            [],
            ReportAudience.SELF,
            esc_state=EscalationState.CONCERN,
            consecutive_days=3,
            is_vulnerable=False,
        )
        self.assertNotIn("第三天", standard_day3.content)


if __name__ == "__main__":
    unittest.main()
