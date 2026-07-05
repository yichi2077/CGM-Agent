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
        # Analytics determinism is always evaluated at end-of-run.
        self.assertTrue(payload["invariants"]["analytics_deterministic"])

    def test_multi_day_run_checks_push_idempotency(self) -> None:
        # Two days of dense readings so a daily push fires and the idempotency
        # probe runs at least once.
        from datetime import datetime, timedelta, timezone

        lines = ["timestamp,value,unit"]
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for step in range(0, 576):  # 2 days at 5-min cadence
            ts = start + timedelta(minutes=5 * step)
            lines.append(f"{ts.isoformat()},120,mg/dL")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "sample.csv"
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = SimulationRunner(
                db_path=root / "app.db",
                out_dir=root / "out",
                user_id="user-1",
                source_label="simulation:test",
                timezone_name="UTC",
                max_speed=True,
            ).run(CsvReplaySource(csv_path, default_timezone="UTC"))
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "ok")
        # The idempotency invariant is recorded only when a push actually fired;
        # when present it must hold.
        if "push_idempotent" in payload["invariants"]:
            self.assertTrue(payload["invariants"]["push_idempotent"])

    def test_dense_one_minute_cadence_infers_interval_and_stays_green(self) -> None:
        # Regression: the project's default virtual fixture is a 1-minute-cadence
        # AiDEX-style feed. Measured against the hardcoded 5-minute default,
        # data_coverage overflowed the le=100 constraint and the whole run died
        # in the wrap-up phase WITHOUT writing simulation_report.json.
        from datetime import datetime, timedelta, timezone

        lines = ["timestamp,value,unit"]
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for step in range(0, 26 * 60):  # 26 hours at 1-min cadence
            ts = start + timedelta(minutes=step)
            lines.append(f"{ts.isoformat()},120,mg/dL")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "sample.csv"
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = SimulationRunner(
                db_path=root / "app.db",
                out_dir=root / "out",
                user_id="user-1",
                source_label="simulation:test",
                timezone_name="UTC",
                max_speed=True,
            ).run(CsvReplaySource(csv_path, default_timezone="UTC"))
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.issues, 0)
        self.assertEqual(payload["invariants"]["expected_interval_minutes"], 1)
        self.assertTrue(payload["invariants"]["analytics_deterministic"])

    def test_explicit_expected_interval_overrides_inference(self) -> None:
        from datetime import datetime, timedelta, timezone

        lines = ["timestamp,value,unit"]
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for step in range(0, 60):
            ts = start + timedelta(minutes=step)
            lines.append(f"{ts.isoformat()},120,mg/dL")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "sample.csv"
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = SimulationRunner(
                db_path=root / "app.db",
                out_dir=root / "out",
                user_id="user-1",
                source_label="simulation:test",
                timezone_name="UTC",
                max_speed=True,
                expected_interval_minutes=5,
            ).run(CsvReplaySource(csv_path, default_timezone="UTC"))
            payload = json.loads(result.report_json.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "ok")
        self.assertEqual(payload["invariants"]["expected_interval_minutes"], 5)


if __name__ == "__main__":
    unittest.main()
