from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.simulation import CsvReplaySource, StreamIngestor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class StreamIngestorTests(unittest.TestCase):
    def test_archives_batch_and_dedupes_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "sample.csv"
            csv_path.write_text(
                "timestamp,value,unit\n"
                "2026-01-01T00:00:00+00:00,100,mg/dL\n",
                encoding="utf-8",
            )
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repository = SQLiteCGMRepository(store)
            source = CsvReplaySource(csv_path)
            ingestor = StreamIngestor(
                repository=repository,
                user_id="user-1",
                source="simulation:test",
            )

            ingestor.archive_batch(source.batch)
            record = next(source.iter_records()).record
            first = ingestor.ingest_record(record, batch_id=source.batch.batch_id)
            second = ingestor.ingest_record(record, batch_id=source.batch.batch_id)
            stored = repository.list_glucose_points(
                DataScope(
                    user_id="user-1",
                    window_start=next(source.iter_records()).sim_ts,
                    window_end=next(source.iter_records()).sim_ts.replace(minute=5),
                    source="simulation:test",
                )
            )

        self.assertEqual(first.inserted, 1)
        self.assertEqual(second.duplicate, 1)
        self.assertEqual(len(stored), 1)


if __name__ == "__main__":
    unittest.main()
