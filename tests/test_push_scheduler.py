"""P2 tiered-push scheduler + silent-consent tests.

Covers tier-due decision (daily/weekly/monthly), idempotency (no double-push per
period), and the deliberately-narrow silent-consent (candidate -> observing only,
window-respecting, never stable/archived, audited).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import EvidenceRef, HypothesisState, L3Hypothesis
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, new_id
from hermes_cgm_agent.services.scheduling import PushSchedulerConfig, PushSchedulerService
from hermes_cgm_agent.storage.sqlite import SQLiteStore

UTC = timezone.utc


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class PushSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.memory = SQLiteMemoryRepository(self.store)
        self.service = PushSchedulerService(
            store=self.store,
            config=PushSchedulerConfig(
                timezone="UTC", daily_hour=9, weekly_weekday=0, monthly_day=1, silence_days=3
            ),
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── tier decision ─────────────────────────────────────────────────────────
    def test_daily_due_only_after_configured_hour(self) -> None:
        self.assertEqual(self.service.decide_due_tiers(_dt("2026-06-09T08:00:00+00:00"), "u1"), [])
        self.assertEqual(
            self.service.decide_due_tiers(_dt("2026-06-09T09:30:00+00:00"), "u1"), ["daily"]
        )  # Tuesday, day 9 -> daily only

    def test_weekly_due_on_weekday(self) -> None:
        due = self.service.decide_due_tiers(_dt("2026-06-08T09:30:00+00:00"), "u1")  # Monday, day 8
        self.assertEqual(due, ["daily", "weekly"])

    def test_monthly_due_on_first(self) -> None:
        due = self.service.decide_due_tiers(_dt("2026-07-01T09:30:00+00:00"), "u1")  # Wed, day 1
        self.assertIn("monthly", due)
        self.assertIn("daily", due)
        self.assertNotIn("weekly", due)

    # ── idempotency ───────────────────────────────────────────────────────────
    def test_push_is_idempotent_within_period(self) -> None:
        now = _dt("2026-06-09T09:30:00+00:00")
        first = self.service.push_tick(user_id="u1", now=now)
        self.assertEqual([p["tier"] for p in first.pushed], ["daily"])
        later = self.service.push_tick(user_id="u1", now=_dt("2026-06-09T18:00:00+00:00"))
        self.assertEqual(later.pushed, [])  # same daily period -> not re-pushed
        next_day = self.service.push_tick(user_id="u1", now=_dt("2026-06-10T09:30:00+00:00"))
        self.assertEqual([p["tier"] for p in next_day.pushed], ["daily"])  # new period

    # ── silent consent ────────────────────────────────────────────────────────
    def _hyp(self, state: HypothesisState, *, last_checked: datetime) -> str:
        hid = new_id()
        self.memory.upsert_hypothesis(
            L3Hypothesis(
                hypothesis_id=hid,
                user_id="u1",
                statement=f"Recurring pattern {hid[:6]}",
                state=state,
                evidence_count=1,
                evidence_refs=[EvidenceRef(kind="event", ref_id="ev")],
                last_checked=last_checked,
                created_at=last_checked,
                updated_at=last_checked,
            )
        )
        return hid

    def test_silent_consent_advances_stale_candidate(self) -> None:
        now = _dt("2026-06-09T09:30:00+00:00")
        hid = self._hyp(HypothesisState.CANDIDATE, last_checked=now - timedelta(days=5))
        advanced = self.service.apply_silent_consent(user_id="u1", now=now)
        self.assertEqual([a["hypothesis_id"] for a in advanced], [hid])
        reloaded = {h.hypothesis_id: h for h in self.memory.list_hypotheses("u1")}[hid]
        self.assertEqual(reloaded.state, HypothesisState.OBSERVING)

    def test_silent_consent_respects_window(self) -> None:
        now = _dt("2026-06-09T09:30:00+00:00")
        hid = self._hyp(HypothesisState.CANDIDATE, last_checked=now - timedelta(days=1))
        self.assertEqual(self.service.apply_silent_consent(user_id="u1", now=now), [])
        reloaded = {h.hypothesis_id: h for h in self.memory.list_hypotheses("u1")}[hid]
        self.assertEqual(reloaded.state, HypothesisState.CANDIDATE)

    def test_silent_consent_never_touches_stable_or_archived(self) -> None:
        now = _dt("2026-06-09T09:30:00+00:00")
        old = now - timedelta(days=30)
        stable = self._hyp(HypothesisState.STABLE, last_checked=old)
        archived = self._hyp(HypothesisState.ARCHIVED, last_checked=old)
        self.assertEqual(self.service.apply_silent_consent(user_id="u1", now=now), [])
        states = {h.hypothesis_id: h.state for h in self.memory.list_hypotheses("u1")}
        self.assertEqual(states[stable], HypothesisState.STABLE)
        self.assertEqual(states[archived], HypothesisState.ARCHIVED)

    def test_silent_consent_is_audited(self) -> None:
        now = _dt("2026-06-09T09:30:00+00:00")
        self._hyp(HypothesisState.CANDIDATE, last_checked=now - timedelta(days=5))
        self.service.apply_silent_consent(user_id="u1", now=now)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM audit_logs WHERE event_type = 'silent_consent' LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
