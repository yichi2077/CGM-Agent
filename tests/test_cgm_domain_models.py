from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from hermes_cgm_agent.domain import (
    GlucoseAggregate,
    GlucosePoint,
    RawCGMRecord,
    RawImportBatch,
    UserEvent,
    convert_glucose_value,
    EscalationState,
    PendingInteraction,
)


class CGMDomainModelTests(unittest.TestCase):
    def test_glucose_point_exposes_canonical_unit_conversions(self) -> None:
        point = GlucosePoint(
            user_id="user-1",
            timestamp=datetime(2026, 5, 31, 1, 0, tzinfo=timezone.utc),
            value=6.0,
            unit="mmol/L",
            source="sensor:test",
            quality_flag="valid",
        )

        self.assertAlmostEqual(point.value_mg_dl, 108.11, places=2)
        self.assertEqual(point.value_mmol_l, 6.0)

    def test_raw_import_batch_counts_records_and_issues(self) -> None:
        record = RawCGMRecord(
            source_id="sample.csv",
            source_format="csv",
            raw_payload={"timestamp": "2026-05-31T01:00:00Z", "glucose": "108"},
            row_number=2,
        )
        batch = RawImportBatch(
            batch_id="batch-1",
            source_name="sample.csv",
            source_format="csv",
            records=[record],
        )

        self.assertEqual(batch.record_count, 1)
        self.assertEqual(batch.issue_count, 0)

    def test_user_event_matches_predev_aliases(self) -> None:
        event = UserEvent(
            event_id="evt-1",
            user_id="user-1",
            type="meal",
            ts_start=datetime(2026, 5, 31, 2, 0, tzinfo=timezone.utc),
            created_by="agent",
            user_confirmed=False,
            confidence=0.7,
        )

        dumped = event.model_dump(by_alias=True)
        self.assertEqual(dumped["type"], "meal")
        self.assertFalse(dumped["user_confirmed"])

    def test_aggregate_rejects_invalid_window(self) -> None:
        with self.assertRaises(ValidationError):
            GlucoseAggregate(
                user_id="user-1",
                window_start=datetime(2026, 5, 31, 3, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 5, 31, 2, 0, tzinfo=timezone.utc),
                data_coverage=95,
            )

    def test_convert_glucose_value_round_trips(self) -> None:
        mg_dl = convert_glucose_value(6.0, "mmol/L", "mg/dL")
        mmol_l = convert_glucose_value(mg_dl, "mg/dL", "mmol/L")

        self.assertAlmostEqual(mmol_l, 6.0, places=4)

    def test_escalation_state_derivation(self) -> None:
        # Standard escalation
        self.assertEqual(EscalationState.derive(0), EscalationState.NORMAL)
        self.assertEqual(EscalationState.derive(1), EscalationState.NORMAL)
        self.assertEqual(EscalationState.derive(2), EscalationState.NORMAL)
        self.assertEqual(EscalationState.derive(3), EscalationState.CONCERN)
        self.assertEqual(EscalationState.derive(4), EscalationState.CONCERN)
        self.assertEqual(EscalationState.derive(5), EscalationState.EXTERNAL_SUPPORT)
        self.assertEqual(EscalationState.derive(6), EscalationState.EXTERNAL_SUPPORT)

        # Vulnerable escalation (compressed thresholds)
        self.assertEqual(EscalationState.derive(0, is_vulnerable=True), EscalationState.NORMAL)
        self.assertEqual(EscalationState.derive(1, is_vulnerable=True), EscalationState.CONCERN)
        self.assertEqual(EscalationState.derive(2, is_vulnerable=True), EscalationState.CONCERN)
        self.assertEqual(EscalationState.derive(3, is_vulnerable=True), EscalationState.EXTERNAL_SUPPORT)
        self.assertEqual(EscalationState.derive(4, is_vulnerable=True), EscalationState.EXTERNAL_SUPPORT)

    def test_pending_interaction_ttl(self) -> None:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        
        # 1. Active and not expired
        interaction1 = PendingInteraction(
            interaction_id="int-1",
            user_id="user-1",
            interaction_type="missing_data_query",
            content="No data seen today",
            expires_at=now + timedelta(days=3),
        )
        self.assertTrue(interaction1.check_active(now))
        self.assertFalse(interaction1.is_expired)

        # 2. Expired (beyond 3-day TTL)
        interaction2 = PendingInteraction(
            interaction_id="int-2",
            user_id="user-1",
            interaction_type="missing_data_query",
            content="No data seen today",
            expires_at=now - timedelta(seconds=1),
        )
        self.assertFalse(interaction2.check_active(now))
        # Note: is_expired compares against utc_now(), so it's naturally True for past times.
        self.assertTrue(interaction2.is_expired)

        # 3. Resolved
        interaction3 = PendingInteraction(
            interaction_id="int-3",
            user_id="user-1",
            interaction_type="missing_data_query",
            content="No data seen today",
            expires_at=now + timedelta(days=3),
            resolved_at=now,
        )
        self.assertFalse(interaction3.check_active(now))


if __name__ == "__main__":
    unittest.main()

