"""D052 release-hardening regressions: identity chain, rate-noise gate,
DATA_GAP memory pollution, display unit, warm-digest life-language."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.config import default_user_id, display_glucose_unit
from hermes_cgm_agent.domain import DataScope, GlucoseEvent, GlucosePoint, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.analytics import EventDetectionConfig, GlucoseEventDetector
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import ConsolidationService, SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.derive import episodes_from_detected_events
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class DefaultUserIdTests(unittest.TestCase):
    def test_env_override_wins(self) -> None:
        with patch.dict(os.environ, {"CGM_AGENT_USER_ID": "real-user"}, clear=False):
            self.assertEqual(default_user_id(), "real-user")

    def test_fallback_without_env(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CGM_AGENT_USER_ID"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_user_id(), "demo-user")

    def test_executor_fills_missing_user_id_for_push_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            executor = ToolExecutor(
                repository=SQLiteCGMRepository(store), audit_service=AuditService(store)
            )
            with patch.dict(os.environ, {"CGM_AGENT_USER_ID": "real-user"}, clear=False):
                response = executor.execute(
                    tool_name="scheduling.push_tick",
                    arguments={"now": "2026-07-05T09:30:00Z"},
                    session_id="s1",
                )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.payload["user_id"], "real-user")

    def test_executor_fills_missing_user_id_inside_data_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            repo = SQLiteCGMRepository(store)
            with patch.dict(os.environ, {"CGM_AGENT_USER_ID": "real-user"}, clear=False):
                repo.create_glucose_point(
                    GlucosePoint(
                        user_id="real-user",
                        timestamp=datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc),
                        value=110,
                        unit=GlucoseUnit.MG_DL,
                        source="test",
                        quality_flag=QualityFlag.VALID,
                    )
                )
                executor = ToolExecutor(repository=repo, audit_service=AuditService(store))
                response = executor.execute(
                    tool_name="timeseries.get_aggregate",
                    arguments={
                        "data_scope": {
                            "window_start": "2026-07-05T00:00:00",
                            "window_end": "2026-07-06T00:00:00",
                        }
                    },
                    session_id="s1",
                )
        self.assertEqual(response.status, "ok")
        self.assertEqual(response.payload["aggregate"]["point_count"], 1)

    def test_explicit_user_id_is_never_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            executor = ToolExecutor(
                repository=SQLiteCGMRepository(store), audit_service=AuditService(store)
            )
            with patch.dict(os.environ, {"CGM_AGENT_USER_ID": "real-user"}, clear=False):
                response = executor.execute(
                    tool_name="scheduling.push_tick",
                    arguments={"user_id": "someone-else"},
                    session_id="s1",
                )
        self.assertEqual(response.payload["user_id"], "someone-else")


def _minute_points(values: list[float], *, start: datetime, step_minutes: float = 1.0):
    return [
        GlucosePoint(
            user_id="u1",
            timestamp=start + timedelta(minutes=step_minutes * index),
            value=value,
            unit=GlucoseUnit.MG_DL,
            source="test",
            quality_flag=QualityFlag.VALID,
        )
        for index, value in enumerate(values)
    ]


class RapidRateNoiseGateTests(unittest.TestCase):
    START = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

    def _scope(self) -> DataScope:
        return DataScope(
            user_id="u1",
            window_start=self.START - timedelta(hours=1),
            window_end=self.START + timedelta(hours=2),
        )

    def test_one_minute_jitter_is_not_a_rapid_event(self) -> None:
        # +-3.5 mg/dL single-minute wiggles around a flat baseline: rate over
        # any >=5-minute span is far below threshold, so NO rapid events.
        values = [110, 113.5, 110, 106.5, 110, 113.5, 110, 106.5, 110, 113.5, 110]
        detector = GlucoseEventDetector()
        events = detector.detect(points=_minute_points(values, start=self.START), scope=self._scope())
        rapid = [e for e in events if "rapid" in str(e.event_type)]
        self.assertEqual(rapid, [])

    def test_sustained_one_minute_rise_is_still_detected(self) -> None:
        # +4 mg/dL EVERY minute for 12 minutes = sustained 4 mg/dL/min over a
        # >=5-minute span -> must still fire.
        values = [100 + 4 * i for i in range(13)]
        detector = GlucoseEventDetector()
        events = detector.detect(points=_minute_points(values, start=self.START), scope=self._scope())
        rapid = [e for e in events if "rapid_rise" in str(e.event_type)]
        self.assertGreaterEqual(len(rapid), 1)

    def test_five_minute_cadence_behavior_unchanged(self) -> None:
        # Adjacent 5-minute points are exactly rapid_min_span_minutes apart, so
        # the legacy detection (16 mg/dL in 5 min -> 3.2/min) still fires.
        values = [100, 116, 130, 130]
        detector = GlucoseEventDetector()
        events = detector.detect(
            points=_minute_points(values, start=self.START, step_minutes=5.0),
            scope=self._scope(),
        )
        rapid = [e for e in events if "rapid_rise" in str(e.event_type)]
        self.assertEqual(len(rapid), 1)


class DataGapMemoryTests(unittest.TestCase):
    def test_data_gap_events_do_not_become_l1_episodes(self) -> None:
        now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
        gap = GlucoseEvent(
            event_id="g1",
            user_id="u1",
            event_type="data_gap",
            ts_start=now - timedelta(hours=2),
            ts_end=now - timedelta(hours=1),
            severity="info",
            duration_minutes=60,
            point_count=0,
            summary="Data gap of 60 min between valid points.",
        )
        hypo = GlucoseEvent(
            event_id="h1",
            user_id="u1",
            event_type="hypo",
            ts_start=now - timedelta(minutes=30),
            ts_end=now - timedelta(minutes=10),
            severity="warning",
            nadir_value_mg_dl=62,
            duration_minutes=20,
            point_count=5,
            summary="Low glucose episode: nadir 62 mg/dL for 20 min.",
        )
        episodes = episodes_from_detected_events([gap, hypo], now=now)
        self.assertEqual([e.episode_type for e in episodes], ["hypo"])


class DisplayUnitTests(unittest.TestCase):
    def test_default_is_mgdl(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "CGM_AGENT_DISPLAY_UNIT"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(display_glucose_unit(), "mg/dL")

    def test_mmol_spellings_accepted(self) -> None:
        for spelling in ("mmol/L", "mmol/l", "MMOL", "mmoll"):
            with patch.dict(os.environ, {"CGM_AGENT_DISPLAY_UNIT": spelling}, clear=False):
                self.assertEqual(display_glucose_unit(), "mmol/L")

    def test_warm_digest_renders_mmol_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            service = ConsolidationService(repository=SQLiteMemoryRepository(store))
            now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
            with patch.dict(os.environ, {"CGM_AGENT_DISPLAY_UNIT": "mmol/L"}, clear=False):
                summary = service.synthesize_state(
                    user_id="u1",
                    window_start=now - timedelta(days=1),
                    window_end=now,
                    period="daily",
                    metrics_summary={"tir_pct": 88.0, "mean_mgdl": 127.09},
                    now=now,
                )
        self.assertIn("mmol/L", summary.content)
        self.assertIn("7.1", summary.content)
        self.assertNotIn("mg/dL", summary.content)


class WarmDigestLifeLanguageTests(unittest.TestCase):
    def test_hypothesis_statements_are_translated(self) -> None:
        from hermes_cgm_agent.domain import HypothesisState, L3Hypothesis
        from hermes_cgm_agent.services.memory import new_id

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            repo = SQLiteMemoryRepository(store)
            now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
            repo.upsert_hypothesis(
                L3Hypothesis(
                    hypothesis_id=new_id(),
                    user_id="u1",
                    statement="Recurring rapid rise pattern",
                    state=HypothesisState.STABLE,
                    evidence_count=6,
                    last_checked=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            summary = ConsolidationService(repository=repo).synthesize_state(
                user_id="u1",
                window_start=now - timedelta(days=1),
                window_end=now,
                period="daily",
                metrics_summary={"tir_pct": 88.0},
                now=now,
            )
        self.assertIn("上冲片段", summary.content)
        self.assertNotIn("Recurring", summary.content)


if __name__ == "__main__":
    unittest.main()
