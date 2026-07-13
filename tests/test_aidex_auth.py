from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from hermes_cgm_agent.services.aidex import (
    AidexAuthError,
    AidexAuthService,
    AidexConfig,
    AidexTokenResponse,
    AidexTokenStore,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class _Client:
    def build_authorize_url(self, *, state=None):
        return f"https://example/authorize?state={state}"

    def exchange_code(self, code):
        return AidexTokenResponse(
            access_token="access",
            refresh_token="refresh",
            expires_in=86400,
            obtained_at=datetime.now(timezone.utc),
        )


class AidexAuthTests(unittest.TestCase):
    def test_state_is_checked_and_environment_cannot_cross(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(Path(tmp) / "app.db")
            store.initialize()
            token_store = AidexTokenStore(store)
            sandbox = AidexAuthService(
                config=AidexConfig(client_id="id", client_secret="secret"),
                client=_Client(),
                token_store=token_store,
            )
            with self.assertRaisesRegex(AidexAuthError, "state mismatch"):
                sandbox.complete_authorization(
                    "u", "https://callback?code=abc&state=wrong", expected_state="right"
                )
            sandbox.complete_authorization(
                "u", "https://callback?code=abc&state=right", expected_state="right"
            )
            production = AidexAuthService(
                config=AidexConfig(
                    client_id="id", client_secret="secret", use_sandbox=False
                ),
                client=_Client(),
                token_store=token_store,
            )
            with self.assertRaisesRegex(AidexAuthError, "targets production"):
                production.valid_access_token("u")
