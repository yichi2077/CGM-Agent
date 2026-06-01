from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import (
    GlucosePoint,
    GlucoseUnit,
    ImportIssue,
    QualityFlag,
    RawCGMRecord,
    RawImportBatch,
    TimeRange,
    convert_glucose_value,
)


@dataclass(frozen=True)
class NormalizationConfig:
    user_id: str
    source: str
    default_timezone: str = "UTC"
    gap_threshold_minutes: int = 10
    expected_interval_minutes: int = 5
    warmup_until: datetime | None = None
    suspect_low_mg_dl: float = 40
    suspect_high_mg_dl: float = 400


@dataclass(frozen=True)
class NormalizationResult:
    points: list[GlucosePoint]
    issues: list[ImportIssue]
    missing_ranges: list[TimeRange]
    duplicate_count: int


class CGMNormalizer:
    def normalize_batch(
        self,
        batch: RawImportBatch,
        config: NormalizationConfig,
    ) -> NormalizationResult:
        points: list[GlucosePoint] = []
        issues: list[ImportIssue] = []
        duplicate_count = 0
        seen: set[tuple[str, str]] = set()

        for record in batch.records:
            point, point_issues = self._normalize_record(record, config)
            if point_issues:
                issues.extend(point_issues)
                continue
            if point is None:
                continue
            duplicate_key = (point.timestamp.isoformat(), point.source)
            if duplicate_key in seen:
                duplicate_count += 1
                issues.append(
                    ImportIssue(
                        row_number=record.row_number,
                        field="timestamp",
                        message="Duplicate glucose point for source and timestamp",
                        raw_record=record.raw_payload,
                    )
                )
                continue
            seen.add(duplicate_key)
            points.append(point)

        points.sort(key=lambda item: item.timestamp)
        return NormalizationResult(
            points=points,
            issues=issues,
            missing_ranges=self._detect_missing_ranges(points, config),
            duplicate_count=duplicate_count,
        )

    def _normalize_record(
        self,
        record: RawCGMRecord,
        config: NormalizationConfig,
    ) -> tuple[GlucosePoint | None, list[ImportIssue]]:
        issues: list[ImportIssue] = []
        if record.recorded_at is None:
            issues.append(self._issue(record, "recorded_at", "Missing recorded_at"))
        if record.value is None:
            issues.append(self._issue(record, "value", "Missing glucose value"))
        if record.unit is None:
            issues.append(self._issue(record, "unit", "Missing glucose unit"))
        if issues:
            return None, issues

        timestamp = self._to_utc(record.recorded_at, config.default_timezone)
        assert record.value is not None
        assert record.unit is not None
        quality_flag = self._quality_flag(timestamp, record.value, GlucoseUnit(record.unit), config)

        return (
            GlucosePoint(
                user_id=config.user_id,
                timestamp=timestamp,
                value=record.value,
                unit=record.unit,
                source=config.source,
                quality_flag=quality_flag,
                device_id=record.device_id,
                raw_record_id=record.source_record_id,
            ),
            [],
        )

    def _quality_flag(
        self,
        timestamp: datetime,
        value: float,
        unit: GlucoseUnit,
        config: NormalizationConfig,
    ) -> QualityFlag:
        warmup_until = (
            self._to_utc(config.warmup_until, config.default_timezone)
            if config.warmup_until is not None
            else None
        )
        if warmup_until is not None and timestamp < warmup_until:
            return QualityFlag.WARMUP
        value_mg_dl = convert_glucose_value(value, unit, GlucoseUnit.MG_DL)
        if value_mg_dl < config.suspect_low_mg_dl or value_mg_dl > config.suspect_high_mg_dl:
            return QualityFlag.SUSPECT
        return QualityFlag.VALID

    def _detect_missing_ranges(
        self,
        points: list[GlucosePoint],
        config: NormalizationConfig,
    ) -> list[TimeRange]:
        if len(points) < 2:
            return []
        threshold = timedelta(minutes=config.gap_threshold_minutes)
        expected = timedelta(minutes=config.expected_interval_minutes)
        missing_ranges: list[TimeRange] = []
        for previous, current in zip(points, points[1:]):
            delta = current.timestamp - previous.timestamp
            if delta > threshold:
                missing_ranges.append(
                    TimeRange(
                        start=previous.timestamp + expected,
                        end=current.timestamp,
                    )
                )
        return missing_ranges

    @staticmethod
    def _to_utc(value: datetime, default_timezone: str) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(default_timezone))
        return value.astimezone(timezone.utc).replace(microsecond=0)

    @staticmethod
    def _issue(record: RawCGMRecord, field: str, message: str) -> ImportIssue:
        return ImportIssue(
            row_number=record.row_number,
            field=field,
            message=message,
            raw_record=record.raw_payload,
        )
