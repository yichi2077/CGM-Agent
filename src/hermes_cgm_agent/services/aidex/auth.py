from __future__ import annotations

import urllib.parse

from hermes_cgm_agent.services.aidex.client import AidexAuthError, AidexClient
from hermes_cgm_agent.services.aidex.config import AidexConfig
from hermes_cgm_agent.services.aidex.tokens import AidexTokenStore, StoredAidexToken


def extract_authorization_code(code_or_url: str) -> str:
    text = (code_or_url or "").strip()
    if not text:
        raise ValueError("Empty AiDEX authorization code or redirect URL")
    if "?" in text or text.lower().startswith("http"):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        if query.get("error"):
            error = query["error"][0]
            raise AidexAuthError(f"AiDEX authorization was denied: {error}", oauth_error=error)
        if query.get("code") and query["code"][0].strip():
            return query["code"][0].strip()
        raise ValueError("No 'code' parameter found in AiDEX redirect URL")
    return text


class AidexAuthService:
    def __init__(
        self,
        *,
        config: AidexConfig,
        client: AidexClient,
        token_store: AidexTokenStore,
    ) -> None:
        self.config = config
        self.client = client
        self.token_store = token_store

    def authorization_url(self, *, state: str | None = None) -> str:
        return self.client.build_authorize_url(state=state)

    def complete_authorization(
        self,
        user_id: str,
        code_or_url: str,
        *,
        expected_state: str | None = None,
    ) -> StoredAidexToken:
        if expected_state is not None and ("?" in code_or_url or "://" in code_or_url):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(code_or_url).query)
            if query.get("state", [None])[0] != expected_state:
                raise AidexAuthError("AiDEX OAuth state mismatch — possible CSRF attack")
        token = self.client.exchange_code(extract_authorization_code(code_or_url))
        return self.token_store.save(user_id, token, environment=self.config.environment)

    def valid_access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        stored = self.token_store.load(user_id)
        if stored is None:
            raise AidexAuthError(
                f"No AiDEX authorization found for user '{user_id}'. "
                f"Run `aidex-auth --user-id {user_id}` first."
            )
        if stored.environment != self.config.environment:
            raise AidexAuthError(
                f"Stored AiDEX authorization is for {stored.environment}, but the "
                f"current configuration targets {self.config.environment}; re-run "
                "aidex-auth for the selected environment."
            )
        if not force_refresh and not stored.is_expired():
            return stored.access_token
        try:
            token = self.client.refresh_token(stored.refresh_token)
        except AidexAuthError as exc:
            raise AidexAuthError(
                f"AiDEX token refresh failed for user '{user_id}'; re-run aidex-auth.",
                oauth_error=exc.oauth_error,
            ) from exc
        return self.token_store.save(
            user_id, token, environment=self.config.environment
        ).access_token
