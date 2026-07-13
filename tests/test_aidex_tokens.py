from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.services.aidex import AidexTokenResponse, AidexTokenStore
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AidexTokenStoreTests(unittest.TestCase):
    def test_tokens_are_encrypted_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            token_store = AidexTokenStore(store)
            saved = token_store.save(
                "user-1",
                AidexTokenResponse(
                    access_token="secret-access",
                    refresh_token="secret-refresh",
                    expires_in=86400,
                    obtained_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
                ),
                environment="sandbox",
            )
            self.assertEqual(saved.access_token, "secret-access")
            with store.connect() as conn:
                row = conn.execute(
                    "SELECT access_token, refresh_token FROM aidex_tokens WHERE user_id = ?",
                    ("user-1",),
                ).fetchone()
            self.assertTrue(row["access_token"].startswith("enc:v1:"))
            self.assertNotIn("secret-access", row["access_token"])
            self.assertNotIn("secret-refresh", row["refresh_token"])
