from __future__ import annotations

import urllib.parse

from hermes_cgm_agent.services.dexcom.client import DexcomAuthError, DexcomClient
from hermes_cgm_agent.services.dexcom.config import DexcomConfig
from hermes_cgm_agent.services.dexcom.tokens import DexcomTokenStore, StoredDexcomToken


def extract_authorization_code(code_or_url: str) -> str:
    """Accept either a bare authorization code or the full redirect URL the
    Dexcom login flow lands on (``https://...?code=XYZ&state=...``) and return
    the code. Keeps the CLI forgiving about what the user pastes back."""
    text = (code_or_url or "").strip()
    if not text:
        raise ValueError("Empty authorization code or redirect URL")
    if "?" in text or text.lower().startswith("http"):
        parsed = urllib.parse.urlparse(text)
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("error"):
            raise DexcomAuthError(
                f"Authorization was denied: {query['error'][0]}",
                oauth_error=query["error"][0],
            )
        codes = query.get("code")
        if codes and codes[0].strip():
            return codes[0].strip()
        raise ValueError(f"No 'code' parameter found in redirect URL: {text}")
    return text


class DexcomAuthService:
    """Owns the OAuth lifecycle: authorize URL, code exchange, and returning a
    valid (auto-refreshed) access token to data callers."""

    def __init__(
        self,
        *,
        config: DexcomConfig,
        client: DexcomClient,
        token_store: DexcomTokenStore,
    ) -> None:
        self.config = config
        self.client = client
        self.token_store = token_store

    def authorization_url(self, *, state: str | None = None) -> str:
        return self.client.build_authorize_url(state=state)

    def complete_authorization(self, user_id: str, code_or_url: str) -> StoredDexcomToken:
        code = extract_authorization_code(code_or_url)
        token = self.client.exchange_code(code)
        return self.token_store.save(user_id, token, environment=self.config.environment)

    def has_token(self, user_id: str) -> bool:
        return self.token_store.load(user_id) is not None

    def valid_access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        stored = self.token_store.load(user_id)
        if stored is None:
            raise DexcomAuthError(
                f"No Dexcom authorization found for user '{user_id}'. "
                "Run `dexcom-auth --user-id {user_id}` to authorize."
            )
        if not force_refresh and not stored.is_expired():
            return stored.access_token
        return self._refresh(stored).access_token

    def _refresh(self, stored: StoredDexcomToken) -> StoredDexcomToken:
        try:
            token = self.client.refresh_token(stored.refresh_token)
        except DexcomAuthError as exc:
            # A failed refresh (revoked/expired refresh_token) is unrecoverable
            # without a fresh browser authorization — surface that clearly.
            raise DexcomAuthError(
                f"Dexcom token refresh failed for user '{stored.user_id}'; "
                "re-run dexcom-auth to re-authorize.",
                oauth_error=exc.oauth_error,
            ) from exc
        return self.token_store.save(stored.user_id, token, environment=self.config.environment)
