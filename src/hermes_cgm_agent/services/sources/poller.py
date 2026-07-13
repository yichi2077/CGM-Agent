from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from hermes_cgm_agent.domain import (
    DataScope,
    GlucosePoint,
    GlucoseUnit,
    QualityFlag,
    RawCGMRecord,
    RawImportBatch,
    ImportIssue,
    convert_glucose_value,
)
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.analytics import EventDetectionConfig, GlucoseEventDetector
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import (
    SQLiteMemoryRepository,
    StreamMemoryConfig,
    StreamMemoryService,
)
from hermes_cgm_agent.services.sources.http import HTTPSourceClient
from hermes_cgm_agent.services.sources.models import SourceKind, SourcePollResult
from hermes_cgm_agent.services.sources.parser import parse_source_payload


class SourceHTTPClient(Protocol):
    def fetch_json(self, *, url: str, kind: SourceKind, count: int) -> tuple[str, Any]: ...


@dataclass(frozen=True)
class SourcePollConfig:
    expected_interval_minutes: int = 5
    event_lookback_minutes: int = 60
    suspect_low_mg_dl: float = 40.0
    suspect_high_mg_dl: float = 400.0
    auto_memory_enabled: bool = True
    warm_summary_min_interval_minutes: int = 60
    max_stale_minutes: int = 12
    max_future_clock_skew_minutes: int = 5


class SourcePollService:
    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        client: SourceHTTPClient | None = None,
        detector: GlucoseEventDetector | None = None,
        config: SourcePollConfig | None = None,
        memory_service: StreamMemoryService | None = None,
    ) -> None:
        self.repository = repository
        self.client = client or HTTPSourceClient()
        self.config = config or SourcePollConfig()
        self.detector = detector or GlucoseEventDetector(
            EventDetectionConfig(expected_interval_minutes=self.config.expected_interval_minutes)
        )
        self.memory_service = memory_service

    def poll(
        self,
        *,
        user_id: str,
        kind: SourceKind,
        url: str,
        count: int = 12,
        source: str | None = None,
        received_at: datetime | None = None,
    ) -> SourcePollResult:
        if not user_id.strip():
            raise ValueError("user_id is required")
        received = _as_utc(received_at or utc_now())
        resolved_url, payload = self.client.fetch_json(url=url, kind=kind, count=count)
        parsed = parse_source_payload(payload, kind=kind)
        issues = list(parsed.issues)
        accepted_readings = []
        future_limit = received + timedelta(minutes=self.config.max_future_clock_skew_minutes)
        for index, reading in enumerate(parsed.readings, start=1):
            if reading.measured_at > future_limit:
                issues.append(
                    ImportIssue(
                        row_number=index,
                        message=(
                            "Reading timestamp exceeds allowed future clock skew; "
                            "raw row retained but normalized point rejected"
                        ),
                        raw_record=reading.raw_payload,
                    )
                )
            else:
                accepted_readings.append(reading)
        source_label = source or _source_label(kind, resolved_url)
        batch_id = f"poll-{uuid.uuid4().hex}"
        batch = RawImportBatch(
            batch_id=batch_id,
            source_name=source_label,
            source_format="api",
            imported_at=received,
            records=[
                RawCGMRecord(
                    source_id=resolved_url,
                    source_format="api",
                    raw_payload=reading.raw_payload,
                    row_number=index,
                    recorded_at=reading.measured_at,
                    value=reading.value,
                    unit=reading.unit,
                    device_id=reading.device_id,
                    source_record_id=reading.source_record_id,
                )
                for index, reading in enumerate(parsed.readings, start=1)
            ],
            issues=issues,
        )
        self.repository.create_import_batch(batch)

        inserted = 0
        duplicate = 0
        for reading in accepted_readings:
            point = GlucosePoint(
                user_id=user_id,
                timestamp=reading.measured_at,
                received_at=received,
                value=reading.value,
                unit=reading.unit,
                source=source_label,
                quality_flag=self._quality_flag(reading.value, reading.unit),
                trend=reading.trend,
                device_id=reading.device_id,
                raw_record_id=reading.source_record_id,
            )
            try:
                self.repository.create_glucose_point(point)
                inserted += 1
            except sqlite3.IntegrityError:
                duplicate += 1

        event_count = 0
        event_inserted = 0
        event_duplicate = 0
        inserted_events = []
        if accepted_readings:
            events = self._detect_events(
                user_id=user_id,
                source=source_label,
                readings_measured_at=[reading.measured_at for reading in accepted_readings],
            )
            event_count = len(events)
            for event in events:
                try:
                    self.repository.create_glucose_event(event)
                    event_inserted += 1
                    inserted_events.append(event)
                except sqlite3.IntegrityError:
                    event_duplicate += 1

        if self.config.auto_memory_enabled:
            self._memory_service().ingest_poll_result(
                user_id=user_id,
                source=source_label,
                reading_times=[reading.measured_at for reading in accepted_readings],
                inserted_point_count=inserted,
                inserted_events=inserted_events,
                now=received,
            )

        newest = max(
            (reading.measured_at for reading in parsed.readings),
            default=None,
        )
        age_seconds = (received - newest).total_seconds() if newest else None
        stale = bool(age_seconds is None or age_seconds > self.config.max_stale_minutes * 60)
        future_clock_skew = bool(
            age_seconds is not None and age_seconds < -(self.config.max_future_clock_skew_minutes * 60)
        )

        return SourcePollResult(
            user_id=user_id,
            kind=kind,
            url=resolved_url,
            source=source_label,
            batch_id=batch_id,
            fetched_count=_payload_count(payload),
            parsed_count=len(parsed.readings),
            inserted_count=inserted,
            duplicate_count=duplicate,
            issue_count=len(issues),
            detected_event_count=event_count,
            detected_event_inserted=event_inserted,
            detected_event_duplicate=event_duplicate,
            received_at=received,
            newest_reading_at=newest,
            newest_reading_age_seconds=age_seconds,
            stale=stale,
            future_clock_skew=future_clock_skew,
        )

    def _memory_service(self) -> StreamMemoryService:
        if self.memory_service is None:
            self.memory_service = StreamMemoryService(
                cgm_repository=self.repository,
                memory_repository=SQLiteMemoryRepository(self.repository.store),
                config=StreamMemoryConfig(
                    expected_interval_minutes=self.config.expected_interval_minutes,
                    warm_refresh_min_interval_minutes=(self.config.warm_summary_min_interval_minutes),
                ),
            )
        return self.memory_service

    def _detect_events(
        self,
        *,
        user_id: str,
        source: str,
        readings_measured_at: list[datetime],
    ):
        start = min(readings_measured_at) - timedelta(minutes=self.config.event_lookback_minutes)
        end = max(readings_measured_at) + timedelta(minutes=self.config.expected_interval_minutes)
        scope = DataScope(user_id=user_id, window_start=start, window_end=end, source=source)
        points = self.repository.list_glucose_points(scope)
        return self.detector.detect(points=points, scope=scope)

    def _quality_flag(self, value: float, unit: GlucoseUnit) -> QualityFlag:
        value_mg_dl = convert_glucose_value(value, unit, GlucoseUnit.MG_DL)
        if value_mg_dl < self.config.suspect_low_mg_dl or value_mg_dl > self.config.suspect_high_mg_dl:
            return QualityFlag.SUSPECT
        return QualityFlag.VALID


def _source_label(kind: SourceKind, url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    path = parsed.path.rstrip("/") or "/"
    return f"{kind}:{host}{path}"


def _payload_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("entries", "records", "sgv"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return len(rows)
        return 1
    return 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)
