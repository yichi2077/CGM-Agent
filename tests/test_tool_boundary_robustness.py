"""Hermes tool-boundary robustness (real-usability hardening).

In the real Hermes runtime the LLM builds tool arguments as ISO strings and
routinely omits the timezone offset. Before this hardening, a naive
``window_start``/``window_end``/``now`` crashed three LLM-facing tools with an
unhandled ``TypeError: can't compare offset-naive and offset-aware datetimes``
that escaped the executor and dumped a raw traceback into the conversation.

Convention under test: **naive datetimes are UTC** — the same rule the SQLite
layer's ``_dt`` serializer has always applied to stored rows — enforced at the
domain boundary (``DataScope``, ``ReportInput``) and the tool handlers, with an
executor-level catch-all so no future defect can leak a traceback to Hermes.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.domain import DataScope, GlucosePoint, GlucoseUnit, QualityFlag
from hermes_cgm_agent.domain.report import ReportInput, ReportType
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.scheduling import PushSchedulerService
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class NaiveDatetimeNormalizationTests(unittest.TestCase):
    def test_data_scope_normalizes_naive_bounds_to_utc(self) -> None:
        scope = DataScope(
            user_id="u1",
            window_start=datetime(2026, 7, 5, 0, 0),
            window_end=datetime(2026, 7, 6, 0, 0),
        )
        self.assertEqual(scope.window_start.tzinfo, timezone.utc)
        self.assertEqual(scope.window_end.tzinfo, timezone.utc)

    def test_data_scope_keeps_aware_bounds_untouched(self) -> None:
        start = datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)
        scope = DataScope(user_id="u1", window_start=start, window_end=end)
        self.assertEqual(scope.window_start, start)
        self.assertEqual(scope.window_end, end)

    def test_report_input_normalizes_naive_anchor_to_utc(self) -> None:
        report_input = ReportInput(
            report_type=ReportType.DAILY,
            user_id="u1",
            anchor_at=datetime(2026, 7, 5, 23, 55),
        )
        self.assertEqual(report_input.anchor_at.tzinfo, timezone.utc)


class _ExecutorFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        store = SQLiteStore(Path(self._tmp.name) / "app.db")
        store.initialize()
        self.repository = SQLiteCGMRepository(store)
        self.repository.create_glucose_point(
            GlucosePoint(
                user_id="u1",
                timestamp=datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc),
                value=110,
                unit=GlucoseUnit.MG_DL,
                source="test",
                quality_flag=QualityFlag.VALID,
            )
        )
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(store),
        )
        self.store = store

    _NAIVE_SCOPE = {
        "user_id": "u1",
        "window_start": "2026-07-05T00:00:00",
        "window_end": "2026-07-06T00:00:00",
    }


class NaiveDatetimeToolCallTests(_ExecutorFixture):
    """The exact argument shapes a Hermes LLM produces must not crash tools."""

    def test_get_aggregate_accepts_naive_window(self) -> None:
        response = self.executor.execute(
            tool_name="timeseries.get_aggregate",
            arguments={"data_scope": dict(self._NAIVE_SCOPE)},
            session_id="s1",
        )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.payload["aggregate"]["point_count"], 1)

    def test_realtime_snapshot_accepts_naive_window_and_naive_now(self) -> None:
        response = self.executor.execute(
            tool_name="timeseries.get_realtime_snapshot",
            arguments={
                "data_scope": dict(self._NAIVE_SCOPE),
                "now": "2026-07-05T03:05:00",
            },
            session_id="s1",
        )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.payload["snapshot"]["latest_glucose_mg_dl"], 110)

    def test_push_tick_accepts_naive_now(self) -> None:
        response = self.executor.execute(
            tool_name="scheduling.push_tick",
            arguments={"user_id": "u1", "now": "2026-07-05T09:30:00"},
            session_id="s1",
        )
        self.assertEqual(response.status, "ok")

    def test_push_tick_naive_now_uses_utc_period_key(self) -> None:
        # 2026-07-05T09:30 naive == UTC == 17:30 Asia/Shanghai: the daily tier
        # gate (>= 09:00 local) passes and the period key must be the LOCAL
        # date derived from the UTC instant, proving naive != system-local.
        scheduler = PushSchedulerService(store=self.store)
        result = scheduler.push_tick(
            user_id="u1", now=datetime(2026, 7, 5, 9, 30)
        )
        self.assertEqual(result.now, "2026-07-05T09:30:00+00:00")


class SafetyRouterNaiveNowTests(unittest.TestCase):
    def test_recovery_window_survives_naive_now(self) -> None:
        # A red-zone event is stored with an aware timestamp; a later evaluate
        # called with a naive `now` (LLM/tool-argument shape) must not raise
        # TypeError when computing the recovery window, and must still surface
        # the recovery double-check.
        from datetime import timedelta

        from hermes_cgm_agent.services.safety import SafetyRouter

        router = SafetyRouter()
        base = datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc)
        scope = DataScope(
            user_id="u1", window_start=base - timedelta(hours=1), window_end=base
        )
        red_point = GlucosePoint(
            user_id="u1",
            timestamp=base - timedelta(minutes=30),
            value=40,
            unit=GlucoseUnit.MG_DL,
            source="test",
            quality_flag=QualityFlag.VALID,
        )
        first = router.evaluate(scope=scope, points=[red_point], now=base)
        self.assertEqual(first.safety_result["status"], "red_zone")

        ok_point = red_point.model_copy(update={"value": 110})
        naive_later = datetime(2026, 7, 5, 3, 30)  # naive == UTC
        second = router.evaluate(scope=scope, points=[ok_point], now=naive_later)
        self.assertIsNotNone(second.recovery_check)
        self.assertTrue(second.recovery_check["recovery_confirmed"])


class ArgumentErgonomicsTests(_ExecutorFixture):
    """D054: realistic LLM argument shapes must not kill the conversation."""

    def test_events_create_folds_unknown_keys_into_payload(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            arguments={
                "user_id": "u1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-07-05T12:00:00",
                    "note": "午餐 面条",
                    "description": "还有一杯奶茶",
                },
            },
            session_id="s1",
        )
        self.assertEqual(response.status, "ok")
        event = response.payload["event"]
        self.assertEqual(event["payload"]["note"], "午餐 面条")
        self.assertEqual(event["payload"]["description"], "还有一杯奶茶")
        # Security fields remain server-forced regardless of folding.
        self.assertEqual(event["created_by"], "agent")
        self.assertFalse(event["user_confirmed"])

    def test_events_create_cannot_smuggle_security_fields(self) -> None:
        response = self.executor.execute(
            tool_name="events.create",
            arguments={
                "user_id": "u1",
                "event": {
                    "type": "meal",
                    "ts_start": "2026-07-05T12:00:00",
                    "created_by": "user",
                    "user_confirmed": True,
                    "event_id": "attacker-chosen",
                },
            },
            session_id="s1",
        )
        self.assertEqual(response.status, "ok")
        event = response.payload["event"]
        self.assertEqual(event["created_by"], "agent")
        self.assertFalse(event["user_confirmed"])
        self.assertNotEqual(event["event_id"], "attacker-chosen")

    def test_missing_required_argument_message_is_actionable(self) -> None:
        response = self.executor.execute(
            tool_name="hypothesis.update",
            arguments={"user_id": "u1", "state": "observing"},
            session_id="s1",
        )
        self.assertEqual(response.status, "error")
        self.assertIn("missing required argument: hypothesis_id", response.payload["error"])


class ExecutorCatchAllTests(_ExecutorFixture):
    """No exception may escape the tool boundary into the Hermes chat."""

    def test_unexpected_handler_exception_returns_structured_error(self) -> None:
        def _boom(*, arguments, session_id):  # noqa: ANN001
            raise RuntimeError("simulated unexpected failure")

        self.executor._get_aggregate = _boom  # type: ignore[method-assign]
        response = self.executor.execute(
            tool_name="timeseries.get_aggregate",
            arguments={"data_scope": dict(self._NAIVE_SCOPE)},
            session_id="s1",
        )
        self.assertEqual(response.status, "error")
        self.assertIn("RuntimeError", response.payload["error"])
        self.assertIsNotNone(response.audit_id)


if __name__ == "__main__":
    unittest.main()
