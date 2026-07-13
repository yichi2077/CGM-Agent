from __future__ import annotations

import unittest
from datetime import datetime, timezone

from hermes_cgm_agent.domain import QualityFlag
from hermes_cgm_agent.services.aidex import AidexConfig, AidexMapper


class AidexMapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mapper = AidexMapper(AidexConfig(client_id="client", client_secret="secret"))
        self.received = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def test_maps_official_sensor_glucose_shape(self) -> None:
        point = self.mapper.sensor_glucose_to_point(
            {
                "appTime": "2026-07-13T01:02:03",
                "glucose": 96,
                "sn": "SN123",
                "status": 0,
                "eventWarning": 0,
            },
            user_id="user-1",
            received_at=self.received,
        )
        assert point is not None
        self.assertEqual(point.value, 96)
        self.assertEqual(point.timestamp.tzinfo, timezone.utc)
        self.assertEqual(point.device_id, "SN123")
        self.assertEqual(point.quality_flag, QualityFlag.VALID)
        self.assertEqual(point.source, "aidex:sandbox")

    def test_preserves_warmup_and_invalid_status_as_quality(self) -> None:
        warmup = self.mapper.sensor_glucose_to_point(
            {"appTime": "2026-07-13T01:02:03", "glucose": 80, "eventWarning": -1},
            user_id="u",
            received_at=self.received,
        )
        suspect = self.mapper.sensor_glucose_to_point(
            {"appTime": "2026-07-13T01:07:03", "glucose": 500, "status": 2},
            user_id="u",
            received_at=self.received,
        )
        assert warmup is not None and suspect is not None
        self.assertEqual(warmup.quality_flag, QualityFlag.WARMUP)
        self.assertEqual(suspect.quality_flag, QualityFlag.SUSPECT)
