from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom.auth import DexcomAuthService
from hermes_cgm_agent.services.dexcom.client import DexcomAuthError, DexcomClient
from hermes_cgm_agent.services.dexcom.config import DexcomConfig
from hermes_cgm_agent.services.dexcom.mapper import DexcomMapper, parse_dexcom_datetime


@dataclass
class DexcomSyncResult:
    user_id: str
    environment: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    egv_fetched: int = 0
    egv_inserted: int = 0
    egv_duplicate: int = 0
    egv_skipped: int = 0
    event_fetched: int = 0
    event_inserted: int = 0
    event_duplicate: int = 0
    event_skipped: int = 0
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "environment": self.environment,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "egv_fetched": self.egv_fetched,
            "egv_inserted": self.egv_inserted,
            "egv_duplicate": self.egv_duplicate,
            "egv_skipped": self.egv_skipped,
            "event_fetched": self.event_fetched,
            "event_inserted": self.event_inserted,
            "event_duplicate": self.event_duplicate,
            "event_skipped": self.event_skipped,
            "issues": list(self.issues),
        }


class DexcomSyncService:
    """High-level sync: resolve the available data window, page through EGVs and
    events under the rate limiter, map them, and persist with dedup."""

    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        auth: DexcomAuthService,
        client: DexcomClient,
        mapper: DexcomMapper,
        config: DexcomConfig,
        chunk_days: int = 7,
    ) -> None:
        self.repository = repository
        self.auth = auth
        self.client = client
        self.mapper = mapper
        self.config = config
        self.chunk_days = max(1, chunk_days)

    def sync(self, *, user_id: str, days: int = 7, force: bool = False) -> DexcomSyncResult:
        if days < 1:
            raise ValueError("days must be >= 1")
        result = DexcomSyncResult(user_id=user_id, environment=self.config.environment)

        # Fail fast with a clear error if the user has not authorized yet.
        self.auth.valid_access_token(user_id)
        data_range = self._call_with_refresh(user_id, lambda token: self.client.get_data_range(token))

        egv_start, egv_end = self._resolve_window(data_range, "egvs", days)
        event_start, event_end = self._resolve_window(data_range, "events", days)
        result.window_start = egv_start
        result.window_end = egv_end

        self._sync_egvs(user_id, egv_start, egv_end, force=force, result=result)
        self._sync_events(user_id, event_start, event_end, force=force, result=result)
        return result

    # -- windowing -----------------------------------------------------------

    def _resolve_window(
        self,
        data_range: dict[str, Any],
        key: str,
        days: int,
    ) -> tuple[datetime, datetime]:
        """Intersect the requested last-N-days window with what Dexcom actually
        has. The sandbox serves a fixed historical window (not "now"), so we
        anchor ``end`` on dataRange's latest record when available and fall back
        to wall-clock for live production accounts with no range yet."""
        now = datetime.now(timezone.utc)
        section = data_range.get(key) if isinstance(data_range, dict) else None
        available_start = _range_edge(section, "start")
        available_end = _range_edge(section, "end")

        end = available_end or now
        start = end - timedelta(days=days)
        if available_start and start < available_start:
            start = available_start
        if start >= end:
            # Degenerate/empty range: widen slightly so the query stays valid.
            start = end - timedelta(days=days)
        return start, end

    # -- EGVs ----------------------------------------------------------------

    def _sync_egvs(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        *,
        force: bool,
        result: DexcomSyncResult,
    ) -> None:
        for chunk_start, chunk_end in _iter_chunks(start, end, self.chunk_days):
            payload = self._call_with_refresh(
                user_id,
                lambda token, s=chunk_start, e=chunk_end: self.client.get_egvs(token, start=s, end=e),
            )
            for record in _records(payload):
                result.egv_fetched += 1
                point = self.mapper.egv_to_point(record, user_id=user_id)
                if point is None:
                    result.egv_skipped += 1
                    continue
                try:
                    self.repository.create_glucose_point(point, replace=force)
                    result.egv_inserted += 1
                except sqlite3.IntegrityError:
                    result.egv_duplicate += 1

    # -- Events --------------------------------------------------------------

    def _sync_events(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        *,
        force: bool,
        result: DexcomSyncResult,
    ) -> None:
        for chunk_start, chunk_end in _iter_chunks(start, end, self.chunk_days):
            payload = self._call_with_refresh(
                user_id,
                lambda token, s=chunk_start, e=chunk_end: self.client.get_events(token, start=s, end=e),
            )
            for record in _records(payload):
                result.event_fetched += 1
                try:
                    event = self.mapper.event_to_user_event(record, user_id=user_id)
                except Exception as exc:  # malformed event payload: skip, don't abort
                    result.event_skipped += 1
                    result.issues.append(f"event map error: {exc}")
                    continue
                if event is None:
                    result.event_skipped += 1
                    continue
                try:
                    self.repository.create_user_event(event, replace=force)
                    result.event_inserted += 1
                except sqlite3.IntegrityError:
                    result.event_duplicate += 1

    # -- auth-aware request --------------------------------------------------

    def _call_with_refresh(self, user_id: str, call):
        """Run a data call with the current token; on a 401 force-refresh once
        and retry. A second failure propagates."""
        token = self.auth.valid_access_token(user_id)
        try:
            return call(token)
        except DexcomAuthError:
            token = self.auth.valid_access_token(user_id, force_refresh=True)
            return call(token)


def _range_edge(section: Any, edge: str) -> datetime | None:
    if not isinstance(section, dict):
        return None
    node = section.get(edge)
    if not isinstance(node, dict):
        return None
    system_time = node.get("systemTime")
    if not system_time:
        return None
    try:
        return parse_dexcom_datetime(system_time)
    except (ValueError, TypeError):
        return None


def _records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _iter_chunks(start: datetime, end: datetime, chunk_days: int):
    span = timedelta(days=chunk_days)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + span, end)
        yield cursor, chunk_end
        cursor = chunk_end


def build_dexcom_sync_service(
    repository: SQLiteCGMRepository,
    *,
    config: DexcomConfig | None = None,
) -> DexcomSyncService:
    """Default wiring used by the CLI and the Hermes tool: build every Dexcom
    collaborator from environment-sourced config and the shared SQLite store."""
    resolved = config or DexcomConfig.from_env()
    client = DexcomClient(resolved)
    from hermes_cgm_agent.services.dexcom.tokens import DexcomTokenStore

    token_store = DexcomTokenStore(repository.store)
    auth = DexcomAuthService(config=resolved, client=client, token_store=token_store)
    return DexcomSyncService(
        repository=repository,
        auth=auth,
        client=client,
        mapper=DexcomMapper(resolved),
        config=resolved,
    )
