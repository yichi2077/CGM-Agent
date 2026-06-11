from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState, L3Hypothesis
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, new_id
from hermes_cgm_agent.services.tools import ToolExecutor, build_default_tool_registry
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class PushTickRegistrationTests(unittest.TestCase):
    """T002 / C1 / FR-001: push_tick is a registered, active, self-contained
    tool following the dotted ``group.action`` naming convention used by every
    other tool (``delivery.send``, ``data.dexcom_sync``) -> ``scheduling.push_tick``."""

    def setUp(self) -> None:
        self.registry = build_default_tool_registry()

    def _spec(self):
        spec = next(
            (s for s in self.registry.list() if s.name == "scheduling.push_tick"),
            None,
        )
        self.assertIsNotNone(spec, "scheduling.push_tick is not registered")
        return spec

    def test_push_tick_is_active_in_scheduling_group(self) -> None:
        names = {spec.name for spec in self.registry.list()}
        self.assertIn("scheduling.push_tick", names)
        spec = self._spec()
        self.assertEqual(spec.group, "scheduling")
        self.assertEqual(spec.status, "active")
        self.assertEqual(spec.owner_module, "push_scheduler")

    def test_push_tick_input_requires_user_id_and_optional_now(self) -> None:
        spec = self._spec()
        schema = spec.input_schema
        self.assertIn("user_id", schema["properties"])
        self.assertIn("user_id", schema["required"])
        self.assertIn("now", schema["properties"])
        self.assertNotIn("now", schema["required"])
        # Self-contained schema: no unresolved $ref/$defs (model must resolve it).
        self.assertNotIn("$ref", json.dumps(schema))

    def test_push_tick_output_exposes_pushed_and_silent_consent(self) -> None:
        spec = self._spec()
        props = spec.output_schema["properties"]
        self.assertIn("pushed", props)
        self.assertIn("silent_consent", props)


class PushTickExecutionTests(unittest.TestCase):
    """US1 (T010/T011/T019b): invoke scheduling.push_tick end-to-end through
    ToolExecutor.execute() against a real store and assert the PushTickResult
    shape, idempotency, the now-override, silent-consent advancement+audit, and
    empty-window robustness. The handler builds PushSchedulerService with the
    default config (timezone Asia/Shanghai, daily_hour=9), so every now override
    below is expressed in +08:00 local time."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.memory = SQLiteMemoryRepository(self.store)
        self.session_id = "push-tick-tool-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _seed_candidate(self, *, last_checked: datetime) -> str:
        hid = new_id()
        self.memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=hid,
                user_id="u1",
                statement=f"Recurring pattern {hid[:6]}",
                state=HypothesisState.CANDIDATE,
                evidence_count=1,
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev")],
                last_checked=last_checked,
                created_at=last_checked,
                updated_at=last_checked,
            )
        )
        return hid

    def _tick(self, now: str) -> dict:
        return self.executor.execute(
            tool_name="scheduling.push_tick",
            session_id=self.session_id,
            arguments={"user_id": "u1", "now": now},
        ).to_dict()

    def test_execute_returns_pushtickresult_shape(self) -> None:
        # T010(a): a daily push fires (the freshly-seeded candidate trips the
        # daily-trend trigger) and the tool envelope carries the result shape.
        now = "2026-06-09T09:30:00+08:00"  # Tue 09:30 Asia/Shanghai -> daily due
        self._seed_candidate(last_checked=datetime.fromisoformat(now))
        body = self._tick(now)
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["pushed"], list)
        self.assertIsInstance(body["silent_consent"], list)
        self.assertIn("daily", [p["tier"] for p in body["pushed"]])
        daily = next(p for p in body["pushed"] if p["tier"] == "daily")
        for key in ("tier", "period_key", "push_id", "summary_id", "content"):
            self.assertIn(key, daily)

    def test_execute_is_idempotent_within_period(self) -> None:
        # T010(b): a second tick for the same (user, daily, period) pushes nothing.
        now = "2026-06-09T09:30:00+08:00"
        self._seed_candidate(last_checked=datetime.fromisoformat(now))
        first = self._tick(now)
        self.assertEqual([p["tier"] for p in first["pushed"]], ["daily"])
        second = self._tick("2026-06-09T18:00:00+08:00")  # same daily period
        self.assertEqual(second["pushed"], [])

    def test_execute_uses_now_override(self) -> None:
        # T010(c): the ISO-8601 now override is parsed and threaded through.
        now = "2026-06-09T09:30:00+08:00"
        self._seed_candidate(last_checked=datetime.fromisoformat(now))
        body = self._tick(now)
        self.assertEqual(body["now"], "2026-06-09T09:30:00+08:00")

    def test_silent_consent_advances_and_audits_through_tool(self) -> None:
        # T011: a stale candidate advances candidate->observing and is audited,
        # surfaced through the tool's silent_consent list.
        now = "2026-06-09T09:30:00+08:00"
        hid = self._seed_candidate(
            last_checked=datetime.fromisoformat(now) - timedelta(days=5)
        )
        body = self._tick(now)
        self.assertEqual(body["status"], "ok")
        advanced = {a["hypothesis_id"]: a for a in body["silent_consent"]}
        self.assertIn(hid, advanced)
        self.assertEqual(advanced[hid]["to"], "observing")
        reloaded = {h.hypothesis_id: h for h in self.memory.list_hypotheses("u1")}[hid]
        self.assertEqual(reloaded.state, HypothesisState.OBSERVING)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM audit_logs WHERE event_type = 'silent_consent' LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_empty_window_user_ticks_without_error(self) -> None:
        # T019b (analyze L1): a user with no CGM data and no hypotheses still ticks
        # cleanly; a weekly digest (no daily-trend gate) is synthesized from an
        # empty window by ConsolidationService and recorded.
        now = "2026-06-08T09:30:00+08:00"  # Monday -> weekly due
        body = self._tick(now)
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["pushed"], list)
        self.assertIn("weekly", [p["tier"] for p in body["pushed"]])
        weekly = next(p for p in body["pushed"] if p["tier"] == "weekly")
        self.assertIsInstance(weekly["content"], str)


if __name__ == "__main__":
    unittest.main()
