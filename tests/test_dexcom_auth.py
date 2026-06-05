from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_cgm_agent.services.dexcom import (
    DexcomAuthError,
    DexcomAuthService,
    DexcomConfig,
    DexcomTokenStore,
    TokenResponse,
    extract_authorization_code,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _config() -> DexcomConfig:
    return DexcomConfig(client_id="cid", client_secret="secret", use_sandbox=True)


def _token(access: str, refresh: str, *, expires_in: int = 7200, obtained_at: datetime | None = None) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        obtained_at=obtained_at or datetime.now(timezone.utc),
    )


class FakeClient:
    def __init__(self, *, exchange=None, refresh=None) -> None:
        self.exchange_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self._exchange = exchange
        self._refresh = refresh

    def build_authorize_url(self, *, state=None) -> str:
        return f"https://auth.example/login?state={state}"

    def exchange_code(self, code: str) -> TokenResponse:
        self.exchange_calls.append(code)
        if isinstance(self._exchange, Exception):
            raise self._exchange
        assert self._exchange is not None
        return self._exchange

    def refresh_token(self, refresh_token: str) -> TokenResponse:
        self.refresh_calls.append(refresh_token)
        if isinstance(self._refresh, Exception):
            raise self._refresh
        assert self._refresh is not None
        return self._refresh


class ExtractAuthorizationCodeTests(unittest.TestCase):
    def test_extracts_bare_code(self) -> None:
        self.assertEqual(extract_authorization_code("abc123"), "abc123")

    def test_extracts_code_from_redirect_url(self) -> None:
        url = "https://www.google.com/?code=abc123&state=xyz"
        self.assertEqual(extract_authorization_code(url), "abc123")

    def test_error_in_redirect_raises(self) -> None:
        with self.assertRaises(DexcomAuthError):
            extract_authorization_code("https://cb/?error=access_denied")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            extract_authorization_code("   ")


class DexcomTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_and_load_round_trip(self) -> None:
        token_store = DexcomTokenStore(self.store)
        token_store.save("user-1", _token("access-A", "refresh-A"), environment="sandbox")
        loaded = token_store.load("user-1")
        assert loaded is not None
        self.assertEqual(loaded.access_token, "access-A")
        self.assertEqual(loaded.refresh_token, "refresh-A")
        self.assertEqual(loaded.environment, "sandbox")

    def test_tokens_are_encrypted_at_rest(self) -> None:
        token_store = DexcomTokenStore(self.store)
        token_store.save("user-1", _token("super-secret-access", "super-secret-refresh"), environment="sandbox")
        raw = self.db_path.read_bytes()
        self.assertNotIn(b"super-secret-access", raw)
        self.assertNotIn(b"super-secret-refresh", raw)

    def test_save_upserts_existing_user(self) -> None:
        token_store = DexcomTokenStore(self.store)
        token_store.save("user-1", _token("a1", "r1"), environment="sandbox")
        token_store.save("user-1", _token("a2", "r2"), environment="sandbox")
        loaded = token_store.load("user-1")
        assert loaded is not None
        self.assertEqual(loaded.access_token, "a2")


class DexcomAuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.token_store = DexcomTokenStore(self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _auth(self, client) -> DexcomAuthService:
        return DexcomAuthService(config=_config(), client=client, token_store=self.token_store)

    def test_complete_authorization_stores_token(self) -> None:
        client = FakeClient(exchange=_token("at", "rt"))
        auth = self._auth(client)
        stored = auth.complete_authorization("user-1", "https://cb/?code=the-code")
        self.assertEqual(client.exchange_calls, ["the-code"])
        self.assertEqual(stored.access_token, "at")
        self.assertEqual(self.token_store.load("user-1").access_token, "at")

    def test_valid_access_token_returns_stored_when_fresh(self) -> None:
        client = FakeClient(refresh=_token("SHOULD-NOT", "x"))
        self.token_store.save("user-1", _token("fresh-access", "rt"), environment="sandbox")
        auth = self._auth(client)
        self.assertEqual(auth.valid_access_token("user-1"), "fresh-access")
        self.assertEqual(client.refresh_calls, [])

    def test_valid_access_token_refreshes_when_expired(self) -> None:
        client = FakeClient(refresh=_token("new-access", "new-refresh"))
        expired = _token("old-access", "old-refresh", obtained_at=datetime.now(timezone.utc) - timedelta(hours=3))
        self.token_store.save("user-1", expired, environment="sandbox")
        auth = self._auth(client)
        self.assertEqual(auth.valid_access_token("user-1"), "new-access")
        self.assertEqual(client.refresh_calls, ["old-refresh"])
        # refreshed token is persisted
        self.assertEqual(self.token_store.load("user-1").access_token, "new-access")

    def test_force_refresh_even_when_fresh(self) -> None:
        client = FakeClient(refresh=_token("forced", "rt2"))
        self.token_store.save("user-1", _token("fresh", "rt"), environment="sandbox")
        auth = self._auth(client)
        self.assertEqual(auth.valid_access_token("user-1", force_refresh=True), "forced")

    def test_missing_token_raises(self) -> None:
        auth = self._auth(FakeClient())
        with self.assertRaises(DexcomAuthError):
            auth.valid_access_token("nobody")

    def test_refresh_failure_raises_auth_error(self) -> None:
        client = FakeClient(refresh=DexcomAuthError("invalid_grant", oauth_error="invalid_grant"))
        expired = _token("old", "old-rt", obtained_at=datetime.now(timezone.utc) - timedelta(hours=3))
        self.token_store.save("user-1", expired, environment="sandbox")
        auth = self._auth(client)
        with self.assertRaises(DexcomAuthError):
            auth.valid_access_token("user-1")


if __name__ == "__main__":
    unittest.main()
