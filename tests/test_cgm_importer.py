from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.data import CGMImporter, FieldMapping, SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


FIXTURES = Path(__file__).parent / "fixtures"


class CGMImporterTests(unittest.TestCase):
    def test_csv_fixture_imports_records_and_issues(self) -> None:
        importer = CGMImporter(
            FieldMapping(
                timestamp="timestamp",
                value="glucose",
                unit="unit",
                device_id="device_id",
                source_record_id="record_id",
            )
        )

        batch = importer.import_csv(FIXTURES / "sample_cgm.csv", batch_id="csv-batch")

        self.assertEqual(batch.batch_id, "csv-batch")
        self.assertEqual(batch.source_format, "csv")
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.issue_count, 3)
        self.assertEqual(batch.records[0].source_record_id, "csv-1")
        self.assertEqual(batch.records[0].value, 108)
        self.assertEqual(batch.records[1].unit, "mmol/L")
        self.assertEqual(
            [(issue.row_number, issue.field) for issue in batch.issues],
            [(4, "timestamp"), (5, "glucose"), (6, "timestamp")],
        )

    def test_json_fixture_imports_records_and_issues(self) -> None:
        importer = CGMImporter()

        batch = importer.import_json(FIXTURES / "sample_cgm.json", batch_id="json-batch")

        self.assertEqual(batch.batch_id, "json-batch")
        self.assertEqual(batch.source_format, "json")
        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.issue_count, 2)
        self.assertEqual(batch.records[0].source_record_id, "json-1")
        self.assertEqual(batch.records[1].value, 5.8)
        self.assertEqual(batch.issues[0].field, "value")
        self.assertIn("must be positive", batch.issues[0].message)
        self.assertIsNone(batch.issues[1].field)

    def test_import_file_dispatches_by_extension(self) -> None:
        csv_importer = CGMImporter(FieldMapping(value="glucose"))
        json_importer = CGMImporter()

        csv_batch = csv_importer.import_file(FIXTURES / "sample_cgm.csv", batch_id="csv-dispatch")
        json_batch = json_importer.import_file(FIXTURES / "sample_cgm.json", batch_id="json-dispatch")

        self.assertEqual(csv_batch.source_format, "csv")
        self.assertEqual(json_batch.source_format, "json")

    def test_import_batch_persists_through_repository(self) -> None:
        importer = CGMImporter(FieldMapping(value="glucose"))
        batch = importer.import_csv(FIXTURES / "sample_cgm.csv", batch_id="persisted-batch")
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteStore(Path(temp_dir) / "app.db")
            store.initialize()
            repository = SQLiteCGMRepository(store)

            saved = repository.create_import_batch(batch)
            status = repository.status()

        self.assertEqual(saved.batch_id, "persisted-batch")
        self.assertEqual(saved.record_count, 2)
        self.assertEqual(saved.issue_count, 3)
        self.assertEqual(status.import_batch_count, 1)
        self.assertEqual(status.raw_record_count, 2)
        self.assertEqual(status.import_issue_count, 3)

    def test_import_report_summarizes_batch(self) -> None:
        importer = CGMImporter(FieldMapping(value="glucose"))
        batch = importer.import_csv(FIXTURES / "sample_cgm.csv", batch_id="report-batch")

        report = importer.build_report(batch)

        self.assertEqual(report.batch_id, "report-batch")
        self.assertEqual(report.record_count, 2)
        self.assertEqual(report.issue_count, 3)
        self.assertTrue(report.has_issues)
        self.assertEqual(report.issue_fields, ("glucose", "timestamp"))

    def test_default_unit_supports_sources_without_unit_column(self) -> None:
        importer = CGMImporter(
            FieldMapping(
                timestamp="timestamp",
                value="glucose",
                unit="missing_unit",
                default_unit="mg/dL",
            )
        )

        batch = importer.import_csv(FIXTURES / "sample_cgm.csv", batch_id="default-unit")

        self.assertEqual(batch.record_count, 2)
        self.assertEqual(batch.records[0].unit, "mg/dL")


if __name__ == "__main__":
    unittest.main()
