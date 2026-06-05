from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hermes_cgm_agent.domain import CreatedBy, EventType, GlucoseTrend, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.dexcom import DexcomConfig, DexcomMapper, parse_dexcom_datetime


def _config() -> DexcomConfig:
    return DexcomConfig(client_id="cid", client_secret="secret", use_sandbox=True)


class DexcomDatetimeTests(unittest.TestCase):
    def test_naive_system_time_is_treated_as_utc(self) -> None:
        parsed = parse_dexcom_datetime("2026-05-31T08:30:00")
        self.assertEqual(parsed, datetime(2026, 5, 31, 8, 30, tzinfo=timezone.utc))

    def test_offset_system_time_is_converted_to_utc(self) -> None:
        parsed = parse_dexcom_datetime("2026-05-31T00:30:00-08:00")
        self.assertEqual(parsed, datetime(2026, 5, 31, 8, 30, tzinfo=timezone.utc))

    def test_zulu_suffix_is_handled(self) -> None:
        parsed = parse_dexcom_datetime("2026-05-31T08:30:00Z")
        self.assertEqual(parsed, datetime(2026, 5, 31, 8, 30, tzinfo=timezone.utc))


class DexcomEgvMapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = DexcomMapper(_config())

    def test_maps_core_fields_using_system_time(self) -> None:
        point = self.mapper.egv_to_point(
            {
                "recordId": "egv-1",
                "systemTime": "2026-05-31T08:30:00",
                "displayTime": "2026-05-31T00:30:00",
                "value": 120,
                "unit": "mg/dL",
                "trend": "flat",
                "transmitterId": "TX1",
            },
            user_id="user-1",
        )
        assert point is not None
        self.assertEqual(point.user_id, "user-1")
        # timestamp comes from systemTime (UTC), NOT displayTime (local)
        self.assertEqual(point.timestamp, datetime(2026, 5, 31, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(point.value, 120)
        self.assertEqual(point.unit, GlucoseUnit.MG_DL.value)
        self.assertEqual(point.trend, GlucoseTrend.STABLE.value)
        self.assertEqual(point.quality_flag, QualityFlag.VALID.value)
        self.assertEqual(point.source, "dexcom:sandbox")
        self.assertEqual(point.device_id, "TX1")
        self.assertEqual(point.raw_record_id, "egv-1")

    def test_trend_mapping_covers_all_dexcom_values(self) -> None:
        cases = {
            "doubleUp": GlucoseTrend.RISING_FAST,
            "singleUp": GlucoseTrend.RISING,
            "fortyFiveUp": GlucoseTrend.RISING,
            "flat": GlucoseTrend.STABLE,
            "fortyFiveDown": GlucoseTrend.FALLING,
            "singleDown": GlucoseTrend.FALLING,
            "doubleDown": GlucoseTrend.FALLING_FAST,
            "notComputable": GlucoseTrend.UNKNOWN,
            "rateOutOfRange": GlucoseTrend.UNKNOWN,
            "none": GlucoseTrend.UNKNOWN,
        }
        for raw, expected in cases.items():
            point = self.mapper.egv_to_point(
                {"recordId": raw, "systemTime": "2026-05-31T08:30:00", "value": 100, "trend": raw},
                user_id="u",
            )
            assert point is not None
            self.assertEqual(point.trend, expected.value, raw)

    def test_null_value_is_skipped(self) -> None:
        point = self.mapper.egv_to_point(
            {"recordId": "x", "systemTime": "2026-05-31T08:30:00", "value": None, "status": "low"},
            user_id="u",
        )
        self.assertIsNone(point)

    def test_status_low_or_high_marks_suspect(self) -> None:
        for status, value in (("low", 40), ("high", 401)):
            point = self.mapper.egv_to_point(
                {"recordId": status, "systemTime": "2026-05-31T08:30:00", "value": value, "status": status},
                user_id="u",
            )
            assert point is not None
            self.assertEqual(point.quality_flag, QualityFlag.SUSPECT.value, status)

    def test_unknown_unit_defaults_to_mg_dl(self) -> None:
        point = self.mapper.egv_to_point(
            {"recordId": "x", "systemTime": "2026-05-31T08:30:00", "value": 100, "unit": "weird"},
            user_id="u",
        )
        assert point is not None
        self.assertEqual(point.unit, GlucoseUnit.MG_DL.value)


class DexcomEventMapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = DexcomMapper(_config())

    def test_carbs_event_maps_to_meal(self) -> None:
        event = self.mapper.event_to_user_event(
            {
                "recordId": "evt-1",
                "systemTime": "2026-05-31T08:30:00",
                "eventType": "carbs",
                "value": "45",
                "unit": "grams",
                "eventStatus": "created",
            },
            user_id="user-1",
        )
        assert event is not None
        self.assertEqual(event.event_id, "dexcom-evt-evt-1")
        self.assertEqual(event.event_type, EventType.MEAL.value)
        self.assertEqual(event.ts_start, datetime(2026, 5, 31, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(event.created_by, CreatedBy.DEVICE.value)
        self.assertTrue(event.user_confirmed)
        self.assertEqual(event.payload["carbs_grams"], 45.0)

    def test_insulin_event_maps_to_medication(self) -> None:
        event = self.mapper.event_to_user_event(
            {
                "recordId": "evt-2",
                "systemTime": "2026-05-31T08:30:00",
                "eventType": "insulin",
                "eventSubType": "fastActing",
                "value": "5",
                "unit": "units",
            },
            user_id="u",
        )
        assert event is not None
        self.assertEqual(event.event_type, EventType.MEDICATION.value)
        self.assertEqual(event.payload["insulin_units"], 5.0)
        self.assertEqual(event.payload["subtype"], "fastActing")

    def test_exercise_event_sets_end_from_duration(self) -> None:
        event = self.mapper.event_to_user_event(
            {
                "recordId": "evt-3",
                "systemTime": "2026-05-31T08:30:00",
                "eventType": "exercise",
                "value": "30",
                "unit": "minutes",
            },
            user_id="u",
        )
        assert event is not None
        self.assertEqual(event.event_type, EventType.EXERCISE.value)
        self.assertEqual(event.payload["duration_minutes"], 30.0)
        self.assertEqual(event.ts_end, datetime(2026, 5, 31, 9, 0, tzinfo=timezone.utc))

    def test_health_event_maps_to_symptom(self) -> None:
        event = self.mapper.event_to_user_event(
            {
                "recordId": "evt-4",
                "systemTime": "2026-05-31T08:30:00",
                "eventType": "health",
                "eventSubType": "illness",
            },
            user_id="u",
        )
        assert event is not None
        self.assertEqual(event.event_type, EventType.SYMPTOM.value)

    def test_deleted_event_is_skipped(self) -> None:
        event = self.mapper.event_to_user_event(
            {
                "recordId": "evt-5",
                "systemTime": "2026-05-31T08:30:00",
                "eventType": "carbs",
                "eventStatus": "deleted",
            },
            user_id="u",
        )
        self.assertIsNone(event)

    def test_event_without_id_is_skipped(self) -> None:
        event = self.mapper.event_to_user_event(
            {"systemTime": "2026-05-31T08:30:00", "eventType": "carbs"},
            user_id="u",
        )
        self.assertIsNone(event)


if __name__ == "__main__":
    unittest.main()
