from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import (
    DataScope,
    GlucosePoint,
    UserEvent,
    EscalationState,
    HypothesisState,
    L3Hypothesis,
    L2ProfileItem,
    PendingInteraction,
)
from hermes_cgm_agent.domain.report import ReportAudience, ReportInput, ReportType, ReportSourceTrack
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import (
    ReportService,
    SQLiteReportRepository,
)
from hermes_cgm_agent.services.scheduling import (
    PushSchedulerConfig,
    PushSchedulerService,
)
from hermes_cgm_agent.services.scheduling.scheduler import PermissionDenied
from hermes_cgm_agent.storage.sqlite import SQLiteStore
from hermes_cgm_agent.services.reports.narrative_templates import (
    validate_companion_text,
    render_hypothesis_narrative,
    translate_metric,
)


class F4CompanionNarrativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(db_path)
        self.store.initialize()
        self.cgm_repository = SQLiteCGMRepository(self.store)
        self.report_repository = SQLiteReportRepository(self.store)
        self.memory_repository = SQLiteMemoryRepository(self.store)
        self.report_service = ReportService(
            cgm_repository=self.cgm_repository,
            report_repository=self.report_repository,
        )
        self.scheduler_service = PushSchedulerService(
            store=self.store,
            config=PushSchedulerConfig(timezone="UTC", silence_days=3),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_points(self, values: list[int] | None = None) -> None:
        for index, value in enumerate(values or [90, 100, 150, 190]):
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

    def test_tone_isolation_and_metric_translation(self) -> None:
        self._create_points()
        
        # SELF Report: uses life-language, should not contain abbreviations like TIR
        self_report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertNotIn("TIR", self_report.rendered_markdown)
        self.assertNotIn("TAR", self_report.rendered_markdown)
        self.assertIn("大部分时间都在范围里", self_report.rendered_markdown)

        # CLINICIAN Report: uses clinical format
        clinician_report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.CLINICIAN,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertIn("TIR", clinician_report.rendered_markdown)
        self.assertIn("TAR", clinician_report.rendered_markdown)

    def test_hypothesis_conversational_templates(self) -> None:
        # Candidate state template
        res_candidate = render_hypothesis_narrative("candidate", "post lunch spike")
        self.assertEqual(res_candidate, "看起来可能和午餐后血糖偏高有关，你觉得可能是因为这个吗？要不要接下来多留意一下？")

        # Observing state template
        res_observing = render_hypothesis_narrative("observing", "overnight low", evidence_count=3)
        self.assertEqual(res_observing, "在过去几天的记录中，有3次类似于夜间低血糖的情况。我们再观察看看是不是这个规律？")

        # Stable state template
        res_stable = render_hypothesis_narrative("stable", "fasting high")
        self.assertEqual(res_stable, "在你的记录中，空腹血糖偏高这个模式比较常见，这可能是一个比较固定的规律了。")

        # Archived state template
        res_archived = render_hypothesis_narrative("archived", "hypo")
        self.assertEqual(res_archived, "之前关于偏低片段的规律最近不明显了，我们先把它放一边吧。")

    def test_safety_disclaimer_gating_for_vulnerable_population(self) -> None:
        self._create_points()
        
        # Set vulnerable_population=True in L2 memory profile
        self.memory_repository.upsert_profile_item(
            L2ProfileItem(
                item_id="vuln-1",
                user_id="user-1",
                key="vulnerable_population",
                value={"value": True},
            )
        )

        # Render report when disclaimer is NOT acknowledged -> disclaimer gating mode should trigger
        report = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertEqual(report.safety_result["status"], "disclaimer_pending")
        self.assertIn("【安全免责声明】", report.rendered_markdown)
        self.assertIn("若您已阅读并知晓上述内容，请输入“已知晓”以继续查看报告。", report.rendered_markdown)

        # Acknowledge the disclaimer
        self.memory_repository.upsert_profile_item(
            L2ProfileItem(
                item_id="ack-1",
                user_id="user-1",
                key="vulnerable_disclaimer_acknowledged",
                value={"value": True},
            )
        )

        # Render report after disclaimer is acknowledged -> actual report should generate
        report_after = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertEqual(report_after.safety_result["status"], "clear")
        self.assertNotIn("【安全免责声明】", report_after.rendered_markdown)

    def test_progressive_concern_escalation(self) -> None:
        self._create_points()
        
        # Test standard user (non-vulnerable) daily report concern phrasing
        # Day 1 of anomalies (NORMAL)
        rep_normal = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                consecutive_anomaly_days=1,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertNotIn("你还好吗", rep_normal.rendered_markdown)
        self.assertNotIn("跟医生聊聊", rep_normal.rendered_markdown)

        # Day 3 of anomalies (CONCERN)
        rep_concern = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                consecutive_anomaly_days=3,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertIn("最近几天都有点波动，你还好吗？", rep_concern.rendered_markdown)

        # Day 5 of anomalies (EXTERNAL_SUPPORT)
        rep_support = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                consecutive_anomaly_days=5,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertIn("要不要下次复诊时跟医生聊聊？", rep_support.rendered_markdown)

    def test_progressive_concern_escalation_vulnerable(self) -> None:
        self._create_points()
        
        # Set vulnerable flag
        self.memory_repository.upsert_profile_item(
            L2ProfileItem(
                item_id="vuln-1",
                user_id="user-1",
                key="vulnerable_population",
                value={"value": True},
            )
        )
        self.memory_repository.upsert_profile_item(
            L2ProfileItem(
                item_id="ack-1",
                user_id="user-1",
                key="vulnerable_disclaimer_acknowledged",
                value={"value": True},
            )
        )

        # Day 1 of anomalies for vulnerable user (escalated early -> CONCERN)
        rep_concern = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                consecutive_anomaly_days=1,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertIn("最近几天都有点波动，你还好吗？", rep_concern.rendered_markdown)

        # Day 3 of anomalies for vulnerable user (escalated early -> EXTERNAL_SUPPORT)
        rep_support = self.report_service.generate(
            ReportInput(
                report_type="daily",
                user_id="user-1",
                audience=ReportAudience.SELF,
                consecutive_anomaly_days=3,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                },
            )
        )
        self.assertIn("要不要下次复诊时跟医生聊聊？", rep_support.rendered_markdown)

    def _seed_hypothesis(self, hid: str, statement: str, state: HypothesisState, evidence_count: int = 0) -> None:
        now = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        self.memory_repository.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=hid,
                user_id="user-1",
                statement=statement,
                state=state,
                evidence_count=evidence_count,
                last_checked=now,
                created_at=now,
                updated_at=now,
            )
        )

    def _weekly_self(self):
        return self.report_service.generate(
            ReportInput(
                report_type="weekly",
                user_id="user-1",
                audience=ReportAudience.SELF,
                data_scope={
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-07T00:00:00+00:00",
                },
            )
        )

    def test_report_includes_hypothesis_narrative(self) -> None:
        # R001/FR-004: state-aware hypothesis narrative actually reaches the report
        # (regression for F-1: render_hypothesis_narrative was dead code).
        self._create_points()
        self._seed_hypothesis("h-cand", "post lunch spike", HypothesisState.CANDIDATE)
        self._seed_hypothesis("h-obs", "overnight low", HypothesisState.OBSERVING, evidence_count=3)

        report = self._weekly_self()
        self.assertIn("看起来可能和午餐后血糖偏高有关", report.rendered_markdown)
        self.assertIn("在过去几天的记录中，有3次类似于夜间低血糖的情况", report.rendered_markdown)

    def test_red_zone_suppresses_hypothesis_narrative(self) -> None:
        # R003/FR-009: red zone replaces sections wholesale -> no hypothesis leakage.
        self._create_points(values=[40, 45, 50])  # all < 54 mg/dL -> red zone
        self._seed_hypothesis("h-cand", "post lunch spike", HypothesisState.CANDIDATE)

        report = self._weekly_self()
        self.assertEqual(report.safety_result["status"], "red_zone")
        self.assertNotIn("看起来可能和", report.rendered_markdown)

    def test_hypothesis_narrative_personal_track_only(self) -> None:
        # R004/FR-013 + Principle II: hypothesis section carries the personal FACT
        # track only (never the authoritative KB track) and preserves structure.
        self._create_points()
        self._seed_hypothesis("h-cand", "post lunch spike", HypothesisState.CANDIDATE)

        report = self._weekly_self()
        section = next(
            (s for s in report.sections if s.section_id == "hypothesis_narrative"), None
        )
        self.assertIsNotNone(section)
        self.assertEqual(section.source_tracks, [ReportSourceTrack.FACT])
        self.assertEqual(section.confidence, 0.6)

    def test_os_push_denied_fallback(self) -> None:
        # Verify OS push fallback accumulates badge counts correctly when PermissionDenied is raised
        original_send = self.scheduler_service.send_os_push
        def mock_send_denied(user_id, content):
            raise PermissionDenied("OS notifications blocked")
        self.scheduler_service.send_os_push = mock_send_denied

        try:
            now = datetime(2026, 6, 9, 9, 30, 0, tzinfo=timezone.utc)
            # Seed a candidate hypothesis to trigger a daily push
            self.memory_repository.upsert_hypothesis(
                L3Hypothesis(
                    hypothesis_id="hyp-1",
                    user_id="user-1",
                    statement="test pattern",
                    state=HypothesisState.CANDIDATE,
                    last_checked=now,
                    created_at=now,
                    updated_at=now,
                )
            )

            self.assertEqual(self.scheduler_service.get_badge_count("user-1"), 0)
            res = self.scheduler_service.push_tick(user_id="user-1", now=now)
            self.assertEqual([p["tier"] for p in res.pushed], ["daily"])
            self.assertEqual(self.scheduler_service.get_badge_count("user-1"), 1)
        finally:
            self.scheduler_service.send_os_push = original_send


if __name__ == "__main__":
    unittest.main()
