from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.services.simulation import CsvReplaySource


class CsvReplaySourceTests(unittest.TestCase):
    def test_replays_sorted_original_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sample.csv"
            csv_path.write_text(
                "timestamp,value,unit,record_id\n"
                "2026-01-01T00:05:00+00:00,101,mg/dL,b\n"
                "2026-01-01T00:00:00+00:00,100,mg/dL,a\n",
                encoding="utf-8",
            )

            records = list(CsvReplaySource(csv_path).iter_records())

        self.assertEqual([item.record.source_record_id for item in records], ["a", "b"])
        self.assertEqual(
            records[0].sim_ts,
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        )

    def test_shift_to_now_preserves_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sample.csv"
            csv_path.write_text(
                "timestamp,value,unit\n"
                "2026-01-01T00:00:00+00:00,100,mg/dL\n"
                "2026-01-01T00:05:00+00:00,101,mg/dL\n",
                encoding="utf-8",
            )

            records = list(
                CsvReplaySource(
                    csv_path,
                    time_base="shift-to-now",
                    now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
                ).iter_records()
            )

        self.assertEqual(records[-1].sim_ts, datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc))
        self.assertEqual((records[1].sim_ts - records[0].sim_ts).total_seconds(), 300)


if __name__ == "__main__":
    unittest.main()
