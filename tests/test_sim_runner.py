from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.simulation import CsvReplaySource, SimulationRunner


class SimulationRunnerTests(unittest.TestCase):
    def test_max_speed_run_writes_audit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "sample.csv"
            csv_path.write_text(
                "timestamp,value,unit\n"
                "2026-01-01T00:00:00+00:00,100,mg/dL\n"
                "2026-01-01T00:05:00+00:00,105,mg/dL\n"
                "2026-01-01T00:10:00+00:00,110,mg/dL\n",
                encoding="utf-8",
            )
            out_dir = root / "out"
            result = SimulationRunner(
                db_path=root / "app.db",
                out_dir=out_dir,
                user_id="user-1",
                source_label="simulation:test",
                max_speed=True,
            ).run(CsvReplaySource(csv_path))

            payload = json.loads(result.report_json.read_text(encoding="utf-8"))
            report_md_exists = result.report_md.exists()

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.inserted, 3)
        self.assertTrue(report_md_exists)
        self.assertTrue(payload["invariants"]["emitted_equals_accounted"])
        self.assertTrue(payload["invariants"]["db_count_matches_inserted"])


if __name__ == "__main__":
    unittest.main()
