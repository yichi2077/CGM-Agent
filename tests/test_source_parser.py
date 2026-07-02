from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

from hermes_cgm_agent.domain import GlucoseTrend, GlucoseUnit
from hermes_cgm_agent.services.sources.http import build_source_url, validate_source_url
from hermes_cgm_agent.services.sources.parser import parse_source_payload


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class SourceParserTests(unittest.TestCase):
    def test_xdrip_sgv_json_preserves_raw_payload_and_duplicate_timestamps(self) -> None:
        measured_at = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        parsed = parse_source_payload(
            [
                {
                    "_id": "x1",
                    "sgv": "105",
                    "date": _epoch_ms(measured_at),
                    "direction": "Flat",
                    "device": "xdrip",
                },
                {
                    "_id": "x2",
                    "sgv": 106,
                    "date": _epoch_ms(measured_at),
                    "direction": "SingleUp",
                },
            ],
            kind="xdrip",
        )

        self.assertEqual(len(parsed.readings), 2)
        self.assertEqual(parsed.issues, [])
        self.assertEqual(parsed.readings[0].measured_at, measured_at)
        self.assertEqual(parsed.readings[0].value, 105)
        self.assertEqual(parsed.readings[0].unit, GlucoseUnit.MG_DL)
        self.assertEqual(parsed.readings[0].trend, GlucoseTrend.STABLE)
        self.assertEqual(parsed.readings[0].source_record_id, "x1")
        self.assertEqual(parsed.readings[0].raw_payload["sgv"], "105")
        self.assertEqual(parsed.readings[1].measured_at, measured_at)
        self.assertEqual(parsed.readings[1].trend, GlucoseTrend.RISING)

    def test_juggluco_payload_accepts_mmol_l_and_arrow_trend(self) -> None:
        parsed = parse_source_payload(
            {
                "sgv": [
                    {
                        "recordId": "j1",
                        "glucose": 6.2,
                        "dateString": "2026-06-01T00:05:00Z",
                        "units": "mmol/L",
                        "trend_arrow": "\u2193",
                    }
                ]
            },
            kind="juggluco",
        )

        self.assertEqual(len(parsed.readings), 1)
        self.assertEqual(parsed.readings[0].unit, GlucoseUnit.MMOL_L)
        self.assertEqual(parsed.readings[0].trend, GlucoseTrend.FALLING)
        self.assertEqual(parsed.readings[0].source_record_id, "j1")

    def test_nightscout_entries_report_missing_fields_as_import_issues(self) -> None:
        parsed = parse_source_payload(
            {
                "entries": [
                    {
                        "_id": "n1",
                        "sgv": 120,
                        "dateString": "2026-06-01T00:10:00+00:00",
                        "direction": "FortyFiveDown",
                    },
                    {
                        "_id": "n2",
                        "dateString": "2026-06-01T00:15:00+00:00",
                    },
                ]
            },
            kind="nightscout",
        )

        self.assertEqual(len(parsed.readings), 1)
        self.assertEqual(parsed.readings[0].trend, GlucoseTrend.FALLING)
        self.assertEqual(len(parsed.issues), 1)
        self.assertIn("Missing glucose value", parsed.issues[0].message)

    def test_source_url_defaults_paths_and_rejects_public_http_by_default(self) -> None:
        original = os.environ.pop("CGM_SOURCE_ALLOW_INSECURE_HTTP", None)
        try:
            self.assertEqual(
                build_source_url(url="http://127.0.0.1:17580", kind="xdrip", count=5),
                "http://127.0.0.1:17580/sgv.json?count=5",
            )
            self.assertEqual(
                build_source_url(url="https://example.test", kind="nightscout", count=3),
                "https://example.test/api/v1/entries/sgv.json?count=3",
            )
            validate_source_url("http://192.168.1.20/sgv.json")
            validate_source_url("https://public.example/sgv.json")
            with self.assertRaisesRegex(ValueError, "localhost/private"):
                validate_source_url("http://public.example/sgv.json")
        finally:
            if original is not None:
                os.environ["CGM_SOURCE_ALLOW_INSECURE_HTTP"] = original


if __name__ == "__main__":
    unittest.main()
