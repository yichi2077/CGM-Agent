from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.services.aidex import (
    AidexConfig,
    AidexMapper,
    AidexSyncService,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class _Auth:
    def valid_access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        return "token"


class _Client:
    def get_data_range(self, token: str):
        return {
            "code": 1,
            "data": {
                "sensorGlucose": {
                    "startTime": "2026-07-12T00:00:00",
                    "endTime": "2026-07-13T00:00:00",
                }
            },
        }

    def get_sensor_glucose(self, token: str, *, start, end):
        return {
            "code": 1,
            "data": [
                {
                    "appTime": "2026-07-12T23:50:00",
                    "glucose": 101,
                    "sn": "SN-1",
                    "status": 0,
                    "eventWarning": 0,
                },
                {
                    "appTime": "2026-07-12T23:55:00",
                    "glucose": 103,
                    "sn": "SN-1",
                    "status": 0,
                    "eventWarning": 0,
                },
            ],
        }


class _Detector:
    def detect(self, *, points, scope):
        return []


class _Memory:
    def __init__(self) -> None:
        self.calls = []

    def ingest_poll_result(self, **kwargs):
        self.calls.append(kwargs)


class AidexSyncTests(unittest.TestCase):
    def test_sync_archives_raw_rows_deduplicates_and_hands_off_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            repository = SQLiteCGMRepository(store)
            config = AidexConfig(client_id="client", client_secret="secret")
            memory = _Memory()
            service = AidexSyncService(
                repository=repository,
                auth=_Auth(),
                client=_Client(),
                mapper=AidexMapper(config),
                config=config,
                detector=_Detector(),
                memory_service=memory,
            )
            received = datetime(2026, 7, 13, 0, 1, tzinfo=timezone.utc)
            first = service.sync(user_id="user-1", received_at=received)
            second = service.sync(
                user_id="user-1", incremental=True, received_at=received
            )
            self.assertEqual(first.inserted_count, 2)
            self.assertEqual(first.duplicate_count, 0)
            self.assertEqual(second.inserted_count, 0)
            self.assertEqual(second.duplicate_count, 2)
            self.assertEqual(memory.calls[0]["inserted_point_count"], 2)
            self.assertEqual(memory.calls[1]["inserted_point_count"], 0)
            with store.connect() as conn:
                points = conn.execute("SELECT COUNT(*) FROM glucose_points").fetchone()[0]
                raw = conn.execute("SELECT COUNT(*) FROM raw_cgm_records").fetchone()[0]
                batches = conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0]
            self.assertEqual(points, 2)
            self.assertEqual(raw, 4)
            self.assertEqual(batches, 2)
