from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from hermes_cgm_agent.services.aidex.client import AidexTokenResponse
from hermes_cgm_agent.storage.sqlite import SQLiteStore, utc_now


@dataclass(frozen=True)
class StoredAidexToken:
    user_id: str
    access_token: str
    refresh_token: str
    expires_at: datetime
    environment: str

    def is_expired(self, *, skew_seconds: int = 60, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return self.expires_at <= moment + timedelta(seconds=skew_seconds)


class AidexTokenStore:
    """Fernet-encrypted persistence for AiDEX OAuth credentials."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def save(
        self, user_id: str, token: AidexTokenResponse, *, environment: str
    ) -> StoredAidexToken:
        now = utc_now()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO aidex_tokens (
                    user_id, access_token, refresh_token, expires_at,
                    environment, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    environment = excluded.environment,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    self.store.seal(token.access_token),
                    self.store.seal(token.refresh_token),
                    token.expires_at.astimezone(timezone.utc).isoformat(),
                    environment,
                    now,
                    now,
                ),
            )
        loaded = self.load(user_id)
        assert loaded is not None
        return loaded

    def load(self, user_id: str) -> StoredAidexToken | None:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, access_token, refresh_token, expires_at, environment
                FROM aidex_tokens WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return StoredAidexToken(
            user_id=row["user_id"],
            access_token=self.store.unseal(row["access_token"]),
            refresh_token=self.store.unseal(row["refresh_token"]),
            expires_at=expires_at.astimezone(timezone.utc),
            environment=row["environment"],
        )

    def delete(self, user_id: str) -> None:
        with self.store.connect() as conn:
            conn.execute("DELETE FROM aidex_tokens WHERE user_id = ?", (user_id,))
