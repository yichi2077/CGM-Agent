"""End-to-end L0→L1→L2→L3 memory chain acceptance through SimulationRunner.

Exercises the full memory pipeline with 5 days of HYPER events:
- L0: real-time context built from CGM data (D038)
- L1: episodic memory from detected glucose events
- L2: user profile beliefs (≥3 same-type distinct days)
- L3: long-term hypotheses (OBSERVING at 3+ days, STABLE at 5+ days)

The fixture uses minimal data per day: 5 HYPER points (210 mg/dL, 08:00–08:20),
1 normal point (120 mg/dL, 08:25), and 1 trigger point (120 mg/dL, 23:55) to
activate the daily memory stage. 5 distinct days triggers L3 STABLE state.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.domain import HypothesisState
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.simulation import CsvReplaySource, SimulationRunner
from hermes_cgm_agent.storage.sqlite import SQLiteStore

_NUM_DAYS = 5  # Minimum for L3 STABLE (l3_stable_threshold=5)


def _generate_hyper_csv(path: Path, num_days: int = _NUM_DAYS) -> None:
    """Minimal fixture: 7 points/day × num_days.

    Per day:
    - 08:00–08:20: 5 HYPER points (210 mg/dL, 20 min > 15 min threshold)
    - 08:25: 1 normal point (120 mg/dL) to end the HYPER zone
    - 23:55: 1 trigger point (120 mg/dL) to activate daily memory
    """
    lines = ["timestamp,value,unit"]
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    for day in range(num_days):
        day_start = start + timedelta(days=day)
        # 5 HYPER points at 5-min cadence (08:00 to 08:20)
        for minute in range(0, 25, 5):
            ts = day_start + timedelta(minutes=minute)
            lines.append(f"{ts.isoformat()},210,mg/dL")
        # Normal point to end the HYPER zone
        ts = day_start + timedelta(minutes=25)
        lines.append(f"{ts.isoformat()},120,mg/dL")
        # Trigger point at 23:55 to activate daily memory
        ts = day_start + timedelta(hours=15, minutes=55)
        lines.append(f"{ts.isoformat()},120,mg/dL")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class MemoryChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.csv_path = self.root / "hyper_5d.csv"
        _generate_hyper_csv(self.csv_path)
        self.db_path = self.root / "app.db"
        self.out_dir = self.root / "out"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_simulation(self, out_dir: Path | None = None) -> dict:
        result = SimulationRunner(
            db_path=self.db_path,
            out_dir=out_dir or self.out_dir,
            user_id="mem-user",
            source_label="simulation:test",
            timezone_name="UTC",
            max_speed=True,
        ).run(CsvReplaySource(self.csv_path, default_timezone="UTC"))
        payload = json.loads(result.report_json.read_text(encoding="utf-8"))
        return {"result": result, "payload": payload}

    def test_full_l0_l3_chain_generated_and_accepted(self) -> None:
        """Single 5-day run: verify L0 built, L1/L2/L3 generated, all acceptance checks pass."""
        run = self._run_simulation()
        result = run["result"]
        payload = run["payload"]

        # Run succeeded
        self.assertEqual(result.status, "ok")
        self.assertTrue(payload["acceptance"]["passed"])

        # L0 built at least once
        self.assertGreater(result.stage_counts.get("l0", 0), 0)

        # L0 content is meaningful
        l0_records = [r for r in payload["timeline"] if r["stage"] == "l0"]
        self.assertGreater(len(l0_records), 0)
        for rec in l0_records:
            self.assertGreater(rec["point_count"], 0)
            self.assertGreater(rec["daily_aggregates"], 0)
            self.assertEqual(rec["data_quality_warnings"], 0)
        self.assertTrue(any(r["key_events"] > 0 for r in l0_records))

        # L1/L2/L3 all generated
        counts = payload["invariants"]["pipeline_counts"]
        self.assertGreater(counts["l1"], 0, "L1 episodes not generated")
        self.assertGreater(counts["l2"], 0, "L2 beliefs not generated")
        self.assertGreater(counts["l3"], 0, "L3 hypotheses not generated")

        # Acceptance checks for L0/L2/L3 all passed
        checks = payload["acceptance"]["checks"]
        self.assertTrue(checks.get("l0_context_built"), "L0 acceptance check failed")
        self.assertTrue(checks.get("l2_belief_generated"), "L2 acceptance check failed")
        self.assertTrue(checks.get("l3_hypothesis_generated"), "L3 acceptance check failed")

        # L2 profile has correct pattern key
        store = SQLiteStore(self.db_path)
        store.initialize()
        repo = SQLiteMemoryRepository(store)
        items = repo.list_profile_items("mem-user", active_only=False)
        self.assertGreater(len(items), 0)
        keys = {item.key for item in items}
        self.assertIn("pattern:hyper", keys)
        hyper_item = next(item for item in items if item.key == "pattern:hyper")
        self.assertGreater(hyper_item.confidence, 0)
        self.assertGreaterEqual(hyper_item.evidence_count, 3)

        # L3 hypothesis reaches STABLE state
        hyps = repo.list_hypotheses("mem-user", active_only=False)
        self.assertGreater(len(hyps), 0)
        hyper_hyp = next(h for h in hyps if "hyper" in h.statement.lower())
        self.assertEqual(hyper_hyp.state, HypothesisState.STABLE)
        self.assertGreaterEqual(hyper_hyp.evidence_count, 5)

    def test_memory_chain_idempotent(self) -> None:
        """Second run against same DB must not duplicate L1/L2/L3."""
        first = self._run_simulation()
        first_counts = first["payload"]["invariants"]["pipeline_counts"]

        second_out = self.root / "out2"
        second = self._run_simulation(out_dir=second_out)
        second_result = second["result"]
        second_counts = second["payload"]["invariants"]["pipeline_counts"]

        self.assertEqual(second_result.status, "ok")
        self.assertEqual(second_result.inserted, 0)
        self.assertEqual(first_counts, second_counts)
        self.assertTrue(
            second["payload"]["acceptance"]["checks"].get("duplicate_replay_downstream_idempotent"),
            "Idempotency check failed on second run",
        )


if __name__ == "__main__":
    unittest.main()
