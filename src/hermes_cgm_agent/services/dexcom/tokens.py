from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from hermes_cgm_agent.services.dexcom.client import TokenResponse
from hermes_cgm_agent.storage.sqlite import SQLiteStore, utc_now


@dataclass(frozen=True)
class StoredDexcomToken:
    user_id: str
    access_token: str
    refresh_token: str
    token_type: str
    scope: str | None
    expires_at: datetime
    environment: str

    def is_expired(self, *, skew_seconds: int = 60, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return self.expires_at <= moment + _seconds(skew_seconds)


def _seconds(value: int):
    from datetime import timedelta

    return timedelta(seconds=value)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DexcomTokenStore:
    """Encrypted persistence for Dexcom OAuth tokens in the shared SQLite store.

    ``access_token`` and ``refresh_token`` are sealed with the project's Fernet
    cipher (same mechanism that protects glucose/event payloads), so the raw
    SQLite file never contains plaintext bearer credentials.
    """

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def save(self, user_id: str, token: TokenResponse, *, environment: str) -> StoredDexcomToken:
        now = utc_now()
        expires_at_iso = token.expires_at.astimezone(timezone.utc).isoformat()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO dexcom_tokens (
                    user_id, access_token, refresh_token, token_type, scope,
                    expires_at, environment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    token_type = excluded.token_type,
                    scope = excluded.scope,
                    expires_at = excluded.expires_at,
                    environment = excluded.environment,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    self.store.seal(token.access_token),
                    self.store.seal(token.refresh_token),
                    token.token_type,
                    self.store.seal(token.scope) if token.scope is not None else None,
                    expires_at_iso,
                    environment,
                    now,
                    now,
                ),
            )
        loaded = self.load(user_id)
        assert loaded is not None
        return loaded

    def load(self, user_id: str) -> StoredDexcomToken | None:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, access_token, refresh_token, token_type, scope,
                       expires_at, environment
                FROM dexcom_tokens
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        scope = row["scope"]
        return StoredDexcomToken(
            user_id=row["user_id"],
            access_token=self.store.unseal(row["access_token"]),
            refresh_token=self.store.unseal(row["refresh_token"]),
            token_type=row["token_type"],
            scope=self.store.unseal(scope) if scope is not None else None,
            expires_at=_parse_dt(row["expires_at"]),
            environment=row["environment"],
        )

    def delete(self, user_id: str) -> None:
        with self.store.connect() as conn:
            conn.execute("DELETE FROM dexcom_tokens WHERE user_id = ?", (user_id,))
