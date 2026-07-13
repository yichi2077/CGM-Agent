from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.cli.bridge import _bridge_poll
from hermes_cgm_agent.services.sources import (
    BridgeConfig,
    HTTPSourceClient,
    check_bridge_health,
    load_bridge_environment,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def fetch_json(self, *, url: str, kind: str, count: int):
        return f"{url}/sgv.json?count={count}", self.payload


class BridgeTests(unittest.TestCase):
    def test_bridge_config_loads_only_scoped_hermes_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text(
                "CGM_AGENT_USER_ID=real-user\n"
                "CGM_BRIDGE_KIND=juggluco\n"
                "CGM_BRIDGE_URL=http://192.168.1.25:17580\n"
                'CGM_BRIDGE_API_SECRET="secret#value"\n'
                "OPENAI_API_KEY=must-not-load\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_bridge_environment(hermes_home=home)
                config = BridgeConfig.from_env()
                self.assertEqual(config.user_id, "real-user")
                self.assertEqual(config.kind, "juggluco")
                self.assertEqual(config.api_secret, "secret#value")
                self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_http_client_authenticates_without_returning_credentials_and_retries(self) -> None:
        requests = []
        sleeps = []

        def urlopen(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.URLError("phone temporarily unreachable")
            return _Response([{"sgv": 100, "date": 1_700_000_000_000}])

        client = HTTPSourceClient(
            api_secret="plain-secret",
            access_token="reader-token",
            retry_attempts=2,
            retry_backoff_seconds=0.25,
            sleep=sleeps.append,
        )
        with patch("urllib.request.urlopen", side_effect=urlopen):
            public_url, payload = client.fetch_json(
                url="http://192.168.1.25:17580",
                kind="juggluco",
                count=3,
            )

        self.assertEqual(len(payload), 1)
        self.assertNotIn("reader-token", public_url)
        self.assertIn("token=reader-token", requests[-1].full_url)
        self.assertEqual(len(requests[-1].headers["Api-secret"]), 40)
        self.assertNotEqual(requests[-1].headers["Api-secret"], "plain-secret")
        self.assertEqual(sleeps, [0.25])

        client_with_url_token = HTTPSourceClient(retry_attempts=1)
        with patch("urllib.request.urlopen", return_value=_Response([])):
            redacted_url, _ = client_with_url_token.fetch_json(
                url="https://nightscout.example?token=must-not-return",
                kind="nightscout",
                count=1,
            )
        self.assertNotIn("must-not-return", redacted_url)

    def test_health_detects_ready_and_stale_phone_data(self) -> None:
        config = BridgeConfig(
            user_id="u",
            kind="juggluco",
            url="http://192.168.1.25:17580",
            source="android:juggluco",
        )
        now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)
        ready = check_bridge_health(
            config,
            client=_FakeClient([{"sgv": 100, "date": int((now - timedelta(minutes=2)).timestamp() * 1000)}]),
            now=now,
        )
        stale = check_bridge_health(
            config,
            client=_FakeClient([{"sgv": 100, "date": int((now - timedelta(minutes=30)).timestamp() * 1000)}]),
            now=now,
        )
        self.assertEqual(ready.status, "ready")
        self.assertFalse(ready.stale)
        self.assertEqual(stale.status, "degraded")
        self.assertTrue(stale.stale)

    def test_bridge_poll_writes_canonical_facts_and_audit(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = [
            {
                "_id": "phone-1",
                "sgv": 105,
                "date": int(now.timestamp() * 1000),
                "direction": "Flat",
            }
        ]
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    "CGM_AGENT_USER_ID": "real-user",
                    "CGM_BRIDGE_KIND": "juggluco",
                    "CGM_BRIDGE_URL": "http://192.168.1.25:17580",
                    "CGM_BRIDGE_SOURCE": "android:juggluco",
                    "CGM_BRIDGE_API_SECRET": "test-secret",
                    "HERMES_HOME": tmp,
                },
                clear=True,
            ),
        ):
            db_path = Path(tmp) / "app.db"
            output = io.StringIO()
            with (
                patch.object(
                    BridgeConfig,
                    "build_client",
                    return_value=_FakeClient(payload),
                ),
                contextlib.redirect_stdout(output),
            ):
                exit_code = _bridge_poll(db_path=db_path)

            body = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(body["inserted_count"], 1)
            con = sqlite3.connect(db_path)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM glucose_points").fetchone()[0], 1)
                row = con.execute(
                    """
                    SELECT event_type, payload_json FROM audit_logs
                    WHERE event_type = 'bridge_poll_completed'
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                con.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "bridge_poll_completed")
            store = SQLiteStore(db_path)
            self.assertNotIn("secret", json.dumps(store.unseal(row[1], legacy="json")))


if __name__ == "__main__":
    unittest.main()
