from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom import (
    DexcomAuthService,
    DexcomConfig,
    DexcomMapper,
    DexcomSyncService,
    DexcomTokenStore,
    TokenResponse,
)
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _config() -> DexcomConfig:
    return DexcomConfig(client_id="cid", client_secret="secret", use_sandbox=True)


DATA_RANGE = {
    "egvs": {
        "start": {"systemTime": "2026-05-31T00:00:00"},
        "end": {"systemTime": "2026-05-31T12:00:00"},
    },
    "events": {
        "start": {"systemTime": "2026-05-31T00:00:00"},
        "end": {"systemTime": "2026-05-31T12:00:00"},
    },
}

EGV_RECORDS = [
    {"recordId": "e1", "systemTime": "2026-05-31T08:00:00", "value": 100, "trend": "flat", "unit": "mg/dL"},
    {"recordId": "e2", "systemTime": "2026-05-31T08:05:00", "value": None, "trend": "none"},  # skipped
    {"recordId": "e3", "systemTime": "2026-05-31T08:10:00", "value": 38, "status": "low",
     "trend": "singleDown", "unit": "mg/dL"},  # suspect
]

EVENT_RECORDS = [
    {"recordId": "c1", "systemTime": "2026-05-31T08:00:00", "eventType": "carbs",
     "value": "45", "unit": "grams", "eventStatus": "created"},
    {"recordId": "d1", "systemTime": "2026-05-31T08:30:00", "eventType": "insulin",
     "eventStatus": "deleted"},  # skipped
]


class FakeDexcomClient:
    def __init__(self, *, data_range=None, egvs=None, events=None) -> None:
        self.data_range = data_range if data_range is not None else DATA_RANGE
        self.egvs = egvs if egvs is not None else EGV_RECORDS
        self.events = events if events is not None else EVENT_RECORDS
        self.egv_calls: list[tuple[datetime, datetime]] = []
        self.event_calls: list[tuple[datetime, datetime]] = []

    def refresh_token(self, refresh_token: str) -> TokenResponse:  # pragma: no cover
        raise AssertionError("refresh should not be needed in these tests")

    def get_data_range(self, token: str) -> dict:
        return self.data_range

    def get_egvs(self, token: str, *, start: datetime, end: datetime) -> dict:
        self.egv_calls.append((start, end))
        return {"records": list(self.egvs)}

    def get_events(self, token: str, *, start: datetime, end: datetime) -> dict:
        self.event_calls.append((start, end))
        return {"records": list(self.events)}


def _seed_token(store: SQLiteStore, user_id: str = "user-1") -> None:
    DexcomTokenStore(store).save(
        user_id,
        TokenResponse(access_token="fresh", refresh_token="rt", expires_in=7200),
        environment="sandbox",
    )


def _build_sync(store: SQLiteStore, client: FakeDexcomClient) -> DexcomSyncService:
    config = _config()
    repository = SQLiteCGMRepository(store)
    token_store = DexcomTokenStore(store)
    auth = DexcomAuthService(config=config, client=client, token_store=token_store)
    return DexcomSyncService(
        repository=repository,
        auth=auth,
        client=client,
        mapper=DexcomMapper(config),
        config=config,
    )


class DexcomConfigEnvTests(unittest.TestCase):
    def test_from_env_reads_credentials_and_sandbox_switch(self) -> None:
        env = {
            "DEXCOM_CLIENT_ID": "abc",
            "DEXCOM_CLIENT_SECRET": "xyz",
            "DEXCOM_REDIRECT_URI": "https://cb",
            "DEXCOM_USE_SANDBOX": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            config = DexcomConfig.from_env()
        self.assertEqual(config.client_id, "abc")
        self.assertEqual(config.base_url, "https://sandbox-api.dexcom.com")
        self.assertEqual(config.source_label, "dexcom:sandbox")

    def test_production_switch(self) -> None:
        env = {
            "DEXCOM_CLIENT_ID": "abc",
            "DEXCOM_CLIENT_SECRET": "xyz",
            "DEXCOM_USE_SANDBOX": "false",
            "DEXCOM_REGION": "",
        }
        with patch.dict(os.environ, env, clear=False):
            config = DexcomConfig.from_env()
        self.assertEqual(config.base_url, "https://api.dexcom.com")
        self.assertEqual(config.environment, "production")

    def test_ous_region_selects_eu_hosts(self) -> None:
        env = {
            "DEXCOM_CLIENT_ID": "abc",
            "DEXCOM_CLIENT_SECRET": "xyz",
            "DEXCOM_USE_SANDBOX": "false",
            "DEXCOM_REGION": "australia",  # alias -> ous
        }
        with patch.dict(os.environ, env, clear=False):
            config = DexcomConfig.from_env()
        self.assertEqual(config.region, "ous")
        self.assertEqual(config.base_url, "https://api.dexcom.eu")

    def test_ous_sandbox_host(self) -> None:
        config = DexcomConfig(client_id="a", client_secret="b", use_sandbox=True, region="ous")
        self.assertEqual(config.base_url, "https://sandbox-api.dexcom.eu")

    def test_base_url_override_wins_over_region_and_sandbox(self) -> None:
        env = {
            "DEXCOM_CLIENT_ID": "abc",
            "DEXCOM_CLIENT_SECRET": "xyz",
            "DEXCOM_REGION": "ous",
            "DEXCOM_USE_SANDBOX": "true",
            "DEXCOM_BASE_URL": "http://127.0.0.1:8473/",  # trailing slash trimmed
        }
        with patch.dict(os.environ, env, clear=False):
            config = DexcomConfig.from_env()
        self.assertEqual(config.base_url, "http://127.0.0.1:8473")

    def test_missing_credentials_raises(self) -> None:
        with patch.dict(os.environ, {"DEXCOM_CLIENT_ID": "", "DEXCOM_CLIENT_SECRET": ""}, clear=False):
            with self.assertRaises(ValueError):
                DexcomConfig.from_env()


class DexcomSyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        _seed_token(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _scope(self) -> DataScope:
        return DataScope(
            user_id="user-1",
            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        )

    def test_sync_persists_points_and_events_with_counts(self) -> None:
        client = FakeDexcomClient()
        result = _build_sync(self.store, client).sync(user_id="user-1", days=7)

        self.assertEqual(result.egv_fetched, 3)
        self.assertEqual(result.egv_inserted, 2)
        self.assertEqual(result.egv_skipped, 1)
        self.assertEqual(result.event_fetched, 2)
        self.assertEqual(result.event_inserted, 1)
        self.assertEqual(result.event_skipped, 1)

        points = self.repository.list_glucose_points(self._scope())
        self.assertEqual(len(points), 2)
        values = sorted(p.value for p in points)
        self.assertEqual(values, [38, 100])

        events = self.repository.list_user_events(self._scope())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["carbs_grams"], 45.0)

    def test_resync_is_idempotent(self) -> None:
        client = FakeDexcomClient()
        sync = _build_sync(self.store, client)
        sync.sync(user_id="user-1", days=7)
        second = sync.sync(user_id="user-1", days=7)

        self.assertEqual(second.egv_inserted, 0)
        self.assertEqual(second.egv_duplicate, 2)
        self.assertEqual(second.event_inserted, 0)
        self.assertEqual(second.event_duplicate, 1)
        # no growth in stored rows
        self.assertEqual(len(self.repository.list_glucose_points(self._scope())), 2)

    def test_force_resync_overwrites(self) -> None:
        client = FakeDexcomClient()
        sync = _build_sync(self.store, client)
        sync.sync(user_id="user-1", days=7)
        forced = sync.sync(user_id="user-1", days=7, force=True)

        self.assertEqual(forced.egv_inserted, 2)
        self.assertEqual(forced.egv_duplicate, 0)
        self.assertEqual(len(self.repository.list_glucose_points(self._scope())), 2)

    def test_window_chunking_splits_large_ranges(self) -> None:
        wide_range = {
            "egvs": {
                "start": {"systemTime": "2026-05-01T00:00:00"},
                "end": {"systemTime": "2026-05-21T00:00:00"},
            },
            "events": {
                "start": {"systemTime": "2026-05-01T00:00:00"},
                "end": {"systemTime": "2026-05-21T00:00:00"},
            },
        }
        client = FakeDexcomClient(data_range=wide_range, egvs=[], events=[])
        _build_sync(self.store, client).sync(user_id="user-1", days=20)
        # 20-day window at 7-day chunks -> 3 EGV requests
        self.assertEqual(len(client.egv_calls), 3)


class DexcomSyncToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        _seed_token(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_executor_runs_dexcom_sync_and_audits(self) -> None:
        client = FakeDexcomClient()
        sync_service = _build_sync(self.store, client)
        executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
            dexcom_sync_factory=lambda repo: sync_service,
        )
        response = executor.execute(
            tool_name="data.dexcom_sync",
            arguments={"user_id": "user-1", "days": 7},
            session_id="sync-session",
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["egv_inserted"], 2)
        self.assertEqual(body["event_inserted"], 1)
        self.assertEqual(body["environment"], "sandbox")
        self.assertIsNotNone(body["audit_id"])

        # acceptance: synced points are queryable through the timeseries tool
        points_response = executor.execute(
            tool_name="timeseries.get_points",
            arguments={
                "data_scope": {
                    "user_id": "user-1",
                    "window_start": "2026-05-31T00:00:00+00:00",
                    "window_end": "2026-06-01T00:00:00+00:00",
                }
            },
            session_id="sync-session",
        )
        self.assertEqual(points_response.status, "ok")
        self.assertEqual(len(points_response.to_dict()["points"]), 2)

    def test_executor_reports_auth_error_when_not_authorized(self) -> None:
        client = FakeDexcomClient()
        # a sync service whose token store has no token for this user
        sync_service = _build_sync(self.store, client)
        executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
            dexcom_sync_factory=lambda repo: sync_service,
        )
        response = executor.execute(
            tool_name="data.dexcom_sync",
            arguments={"user_id": "no-such-user", "days": 7},
            session_id="sync-session",
        )
        body = response.to_dict()
        self.assertEqual(body["status"], "error")
        self.assertIn("authorization", body["error"].lower())


if __name__ == "__main__":
    unittest.main()
