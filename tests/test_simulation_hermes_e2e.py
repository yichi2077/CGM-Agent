from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.simulation import HermesStage


class SimulationHermesE2ETests(unittest.TestCase):
    def test_preflight_is_guarded_when_runtime_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.db"
            result = HermesStage(db_path=db_path, time_base="original", repo_path=Path(temp_dir) / "missing").preflight()

        self.assertEqual(result.status, "preflight_failed")
        self.assertEqual(result.exit_code, 2)

    @unittest.skipUnless(os.getenv("CGM_RUN_HERMES_E2E") == "1", "Hermes runtime E2E is opt-in")
    def test_live_hermes_preflight(self) -> None:
        db_path = Path(os.environ["CGM_AGENT_DB_PATH"])
        result = HermesStage(db_path=db_path, time_base="shift-to-now").preflight()
        self.assertEqual(result.status, "ok")


if __name__ == "__main__":
    unittest.main()
