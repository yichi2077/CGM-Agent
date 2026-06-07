"""P1 end-to-end: data chain -> memory generation -> recall.

Proves the full loop the seed-demo CLI runs: import CGM CSV -> detect glucose
events -> derive L1 episodes dated by their real occurrence -> consolidate to L2
beliefs + L3 hypotheses across distinct days -> recall the consolidated memory.

Uses a tiny deterministic 4-day fixture (one hyper episode per day) so the
recurrence crosses the L2/L3 day thresholds quickly and the test stays fast.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.cli import _seed_demo


def _fixture_csv(path: Path) -> None:
    """4 distinct local days, each with a >180 mg/dL hyper episode (3 consecutive
    5-min points -> 15 covered minutes -> a detected hyper event per day)."""
    lines = ["timestamp,value,unit,device_id,record_id"]
    rid = 0
    for day in (25, 26, 27, 28):  # 2026-04-25..28, 08:00 local = distinct days
        for minute, value in ((0, 210.0), (5, 215.0), (10, 212.0), (240, 110.0)):
            hh = 8 + (minute // 60)
            mm = minute % 60
            ts = f"2026-04-{day:02d}T{hh:02d}:{mm:02d}:00"
            lines.append(f"{ts},{value},mg/dL,SENSOR-T,REC-{rid:04d}")
            rid += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class E2EMemoryRecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work = Path(self.temp_dir.name)
        self.csv = self.work / "fixture.csv"
        self.db = self.work / "seed.db"
        _fixture_csv(self.csv)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_seed_demo(self) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = _seed_demo(
                db_path=self.db,
                csv_path=self.csv,
                user_id="demo-user",
                timezone_name="Asia/Shanghai",
                query="反复出现的高血糖模式 recurring hyper pattern",
            )
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_data_chain_populates_storage(self) -> None:
        payload = self._run_seed_demo()
        data = payload["data_chain"]
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(data["points_inserted"], 16)
        self.assertGreaterEqual(data["detected_events"], 4)  # one hyper per day

    def test_memory_generated_and_consolidated_across_days(self) -> None:
        payload = self._run_seed_demo()
        mem = payload["memory_chain"]
        # L1 episodes derived from detected events, dated by real occurrence.
        self.assertGreaterEqual(mem["l1_episode_total"], 4)
        # recurring same-type across >=3 distinct days -> L2 belief + L3 hypothesis.
        self.assertGreaterEqual(mem["l2_profile_total"], 1)
        self.assertGreaterEqual(mem["l3_hypothesis_total"], 1)
        # B1: warm summary is human-readable narrative, not bare JSON.
        self.assertIn("%", mem["warm_summary"])

    def test_recall_returns_consolidated_memory(self) -> None:
        payload = self._run_seed_demo()
        recall = payload["recall"]
        self.assertGreater(recall["item_count"], 0)
        layers = {item["layer"] for item in recall["items"]}
        # the consolidated belief/hypothesis (hot L2/L3) must be recalled.
        self.assertTrue({"L2", "L3"} & layers)
        # the hyper pattern surfaces somewhere in recalled memory.
        joined = " ".join(item["summary"] for item in recall["items"]).lower()
        self.assertIn("hyper", joined)

    def test_seed_demo_is_idempotent(self) -> None:
        first = self._run_seed_demo()
        second = self._run_seed_demo()
        # re-running must not double-insert points or episodes.
        self.assertEqual(second["data_chain"]["points_inserted"], 0)
        self.assertEqual(second["data_chain"]["points_duplicate"], 16)
        self.assertEqual(
            first["memory_chain"]["l1_episode_total"],
            second["memory_chain"]["l1_episode_total"],
        )


if __name__ == "__main__":
    unittest.main()
