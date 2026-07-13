from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from hermes_cgm_agent.domain import DataScope, GlucoseUnit, ImportIssue, RawCGMRecord, RawImportBatch
from hermes_cgm_agent.domain.cgm import utc_now as domain_utc_now
from hermes_cgm_agent.services.aidex.auth import AidexAuthService
from hermes_cgm_agent.services.aidex.client import AidexAuthError, AidexClient
from hermes_cgm_agent.services.aidex.config import AidexConfig
from hermes_cgm_agent.services.aidex.mapper import AidexMapper, parse_aidex_datetime
from hermes_cgm_agent.services.analytics import EventDetectionConfig, GlucoseEventDetector
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository, StreamMemoryConfig, StreamMemoryService


@dataclass
class AidexSyncResult:
    user_id: str
    environment: str
    source: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    batch_ids: list[str] = field(default_factory=list)
    fetched_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    issue_count: int = 0
    detected_event_count: int = 0
    detected_event_inserted: int = 0
    detected_event_duplicate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "user_id": self.user_id,
            "environment": self.environment,
            "source": self.source,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "batch_ids": list(self.batch_ids),
            "fetched_count": self.fetched_count,
            "inserted_count": self.inserted_count,
            "duplicate_count": self.duplicate_count,
            "skipped_count": self.skipped_count,
            "issue_count": self.issue_count,
            "detected_event_count": self.detected_event_count,
            "detected_event_inserted": self.detected_event_inserted,
            "detected_event_duplicate": self.detected_event_duplicate,
        }


class AidexSyncService:
    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        auth: AidexAuthService,
        client: AidexClient,
        mapper: AidexMapper,
        config: AidexConfig,
        detector: GlucoseEventDetector | None = None,
        memory_service: StreamMemoryService | None = None,
        expected_interval_minutes: int = 5,
    ) -> None:
        self.repository = repository
        self.auth = auth
        self.client = client
        self.mapper = mapper
        self.config = config
        self.expected_interval_minutes = expected_interval_minutes
        self.detector = detector or GlucoseEventDetector(
            EventDetectionConfig(expected_interval_minutes=expected_interval_minutes)
        )
        self.memory_service = memory_service

    def sync(
        self,
        *,
        user_id: str,
        days: int = 1,
        force: bool = False,
        incremental: bool = False,
        overlap_minutes: int = 15,
        bootstrap_hours: int = 24,
        received_at: datetime | None = None,
    ) -> AidexSyncResult:
        if not user_id.strip():
            raise ValueError("user_id is required")
        if days < 1:
            raise ValueError("days must be >= 1")
        received = _as_utc(received_at or domain_utc_now())
        token = self.auth.valid_access_token(user_id)
        data_range = self._call_with_refresh(user_id, token, self.client.get_data_range)
        available_start, available_end = _data_range(data_range)
        if available_end is None:
            available_end = received
        if incremental:
            latest = self._latest_timestamp(user_id)
            if latest is not None:
                start = latest - timedelta(minutes=max(0, overlap_minutes))
            else:
                start = available_end - timedelta(hours=max(1, bootstrap_hours))
        else:
            start = available_end - timedelta(days=days)
        if available_start is not None:
            start = max(start, available_start)
        end = available_end
        if start >= end:
            start = end - timedelta(minutes=max(1, self.expected_interval_minutes))

        result = AidexSyncResult(
            user_id=user_id,
            environment=self.config.environment,
            source=self.config.source_label,
            window_start=start,
            window_end=end,
        )
        inserted_times: list[datetime] = []
        for chunk_start, chunk_end in _iter_chunks(start, end, 30):
            token = self.auth.valid_access_token(user_id)
            payload = self._call_with_refresh(
                user_id,
                token,
                lambda current: self.client.get_sensor_glucose(
                    current, start=chunk_start, end=chunk_end
                ),
            )
            self._ingest_payload(
                payload,
                user_id=user_id,
                force=force,
                received_at=received,
                result=result,
                inserted_times=inserted_times,
            )

        self._detect_and_remember(
            user_id=user_id,
            inserted_times=inserted_times,
            received_at=received,
            result=result,
        )
        return result

    def _ingest_payload(
        self,
        payload: dict[str, Any],
        *,
        user_id: str,
        force: bool,
        received_at: datetime,
        result: AidexSyncResult,
        inserted_times: list[datetime],
    ) -> None:
        rows = payload.get("data")
        if not isinstance(rows, list):
            rows = []
        records: list[RawCGMRecord] = []
        issues: list[ImportIssue] = []
        mapped: list[tuple[dict[str, Any], Any]] = []
        endpoint = f"{self.config.base_url}/v1/user/glu/sensor-glucose"
        for index, raw in enumerate(rows, start=1):
            result.fetched_count += 1
            if not isinstance(raw, dict):
                issues.append(
                    ImportIssue(
                        row_number=index,
                        message="AiDEX sensor-glucose item must be an object",
                        raw_record={"value": raw},
                    )
                )
                continue
            point = self.mapper.sensor_glucose_to_point(
                raw, user_id=user_id, received_at=received_at
            )
            records.append(
                RawCGMRecord(
                    source_id=endpoint,
                    source_format="api",
                    raw_payload=dict(raw),
                    row_number=index,
                    recorded_at=point.timestamp if point is not None else None,
                    value=point.value if point is not None else None,
                    unit=GlucoseUnit.MG_DL if point is not None else None,
                    device_id=point.device_id if point is not None else None,
                    source_record_id=point.raw_record_id if point is not None else None,
                )
            )
            if point is None:
                result.skipped_count += 1
                issues.append(
                    ImportIssue(
                        row_number=index,
                        message="AiDEX record has no usable positive glucose/appTime",
                        raw_record=dict(raw),
                    )
                )
                continue
            mapped.append((raw, point))

        batch_id = f"aidex-{uuid.uuid4().hex}"
        self.repository.create_import_batch(
            RawImportBatch(
                batch_id=batch_id,
                source_name=self.config.source_label,
                source_format="api",
                imported_at=received_at,
                records=records,
                issues=issues,
            )
        )
        result.batch_ids.append(batch_id)
        result.issue_count += len(issues)
        for _, point in mapped:
            try:
                self.repository.create_glucose_point(point, replace=force)
                result.inserted_count += 1
                inserted_times.append(point.timestamp)
            except sqlite3.IntegrityError:
                result.duplicate_count += 1

    def _detect_and_remember(
        self,
        *,
        user_id: str,
        inserted_times: list[datetime],
        received_at: datetime,
        result: AidexSyncResult,
    ) -> None:
        inserted_events = []
        if inserted_times:
            start = min(inserted_times) - timedelta(minutes=60)
            end = max(inserted_times) + timedelta(minutes=self.expected_interval_minutes)
            scope = DataScope(
                user_id=user_id,
                window_start=start,
                window_end=end,
                source=self.config.source_label,
            )
            events = self.detector.detect(
                points=self.repository.list_glucose_points(scope), scope=scope
            )
            result.detected_event_count = len(events)
            for event in events:
                try:
                    self.repository.create_glucose_event(event)
                    result.detected_event_inserted += 1
                    inserted_events.append(event)
                except sqlite3.IntegrityError:
                    result.detected_event_duplicate += 1
        self._memory_service().ingest_poll_result(
            user_id=user_id,
            source=self.config.source_label,
            reading_times=inserted_times,
            inserted_point_count=result.inserted_count,
            inserted_events=inserted_events,
            now=received_at,
        )

    def _memory_service(self) -> StreamMemoryService:
        if self.memory_service is None:
            self.memory_service = StreamMemoryService(
                cgm_repository=self.repository,
                memory_repository=SQLiteMemoryRepository(self.repository.store),
                config=StreamMemoryConfig(
                    expected_interval_minutes=self.expected_interval_minutes
                ),
            )
        return self.memory_service

    def _latest_timestamp(self, user_id: str) -> datetime | None:
        with self.repository.store.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(timestamp) AS latest FROM glucose_points
                WHERE user_id = ? AND source = ?
                """,
                (user_id, self.config.source_label),
            ).fetchone()
        if row is None or not row["latest"]:
            return None
        return parse_aidex_datetime(row["latest"])

    def _call_with_refresh(self, user_id: str, token: str, call):
        try:
            return call(token)
        except AidexAuthError:
            return call(self.auth.valid_access_token(user_id, force_refresh=True))


def _data_range(payload: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    data = payload.get("data") if isinstance(payload, dict) else None
    sensor = data.get("sensorGlucose") if isinstance(data, dict) else None
    if not isinstance(sensor, dict):
        return None, None
    try:
        start = parse_aidex_datetime(sensor["startTime"]) if sensor.get("startTime") else None
        end = parse_aidex_datetime(sensor["endTime"]) if sensor.get("endTime") else None
    except (TypeError, ValueError):
        return None, None
    return start, end


def _iter_chunks(start: datetime, end: datetime, days: int):
    cursor = start
    span = timedelta(days=days)
    while cursor < end:
        chunk_end = min(cursor + span, end)
        yield cursor, chunk_end
        cursor = chunk_end


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)


def build_aidex_sync_service(
    repository: SQLiteCGMRepository, *, config: AidexConfig | None = None
) -> AidexSyncService:
    from hermes_cgm_agent.services.aidex.tokens import AidexTokenStore

    resolved = config or AidexConfig.from_env()
    client = AidexClient(resolved)
    auth = AidexAuthService(
        config=resolved, client=client, token_store=AidexTokenStore(repository.store)
    )
    return AidexSyncService(
        repository=repository,
        auth=auth,
        client=client,
        mapper=AidexMapper(resolved),
        config=resolved,
    )
