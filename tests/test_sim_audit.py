from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.simulation.audit import SimulationAudit
from hermes_cgm_agent.services.simulation import CsvReplaySource, SimulationRunner


class SimulationAuditTests(unittest.TestCase):
    def test_failed_requirement_is_machine_readable_and_fails_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audit = SimulationAudit(run_id="run-1", out_dir=Path(temp_dir))
            audit.require(
                "count_matches",
                False,
                message="count mismatch",
                expected=10,
                actual=9,
            )
            report_json, _ = audit.write()
            payload = json.loads(report_json.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "failed")
        self.assertFalse(payload["acceptance"]["passed"])
        self.assertEqual(
            payload["acceptance"]["comparisons"]["count_matches"],
            {"passed": False, "expected": 10, "actual": 9},
        )
        self.assertEqual(payload["issues"][0]["stage"], "acceptance")

    def test_timeline_and_links_share_run_id(self) -> None:
        audit = SimulationAudit(run_id="run-2", out_dir=Path("unused"))
        audit.record("ingest", correlation_id="reading:1")
        audit.record("report", correlation_id="report:r1")
        audit.link(
            from_stage="ingest",
            from_id="reading:1",
            to_stage="report",
            to_id="r1",
            relation="informed",
        )
        payload = audit.to_dict()

        self.assertEqual([row["sequence"] for row in payload["timeline"]], [1, 2])
        self.assertTrue(all(row["run_id"] == "run-2" for row in payload["timeline"]))
        self.assertEqual(payload["links"][0]["run_id"], "run-2")

    def test_runner_returns_exit_code_1_on_empty_source(self) -> None:
        """Empty source causes runner to return exit_code=1 and status=failed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "empty.csv"
            csv_path.write_text("timestamp,value,unit\n", encoding="utf-8")
            result = SimulationRunner(
                db_path=root / "app.db",
                out_dir=root / "out",
                user_id="user-1",
                source_label="simulation:test",
                max_speed=True,
            ).run(CsvReplaySource(csv_path, default_timezone="UTC"))

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.status, "failed")


if __name__ == "__main__":
    unittest.main()
