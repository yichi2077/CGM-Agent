from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hermes_cgm_agent.domain import GlucoseTrend, GlucoseUnit, ImportIssue, parse_glucose_unit
from hermes_cgm_agent.services.sources.models import ParsedSourcePayload, SourceKind, SourceReading

_VALUE_FIELDS = ("sgv", "glucose", "value")
_TIME_FIELDS = ("date", "dateString", "sysTime", "systemTime", "displayTime")

_TREND_MAP = {
    "doubleup": GlucoseTrend.RISING_FAST,
    "singleup": GlucoseTrend.RISING,
    "fortyfiveup": GlucoseTrend.RISING,
    "flat": GlucoseTrend.STABLE,
    "fortyfivedown": GlucoseTrend.FALLING,
    "singledown": GlucoseTrend.FALLING,
    "doubledown": GlucoseTrend.FALLING_FAST,
    "notcomputable": GlucoseTrend.UNKNOWN,
    "rateoutofrange": GlucoseTrend.UNKNOWN,
    "none": GlucoseTrend.UNKNOWN,
    "\u2192": GlucoseTrend.STABLE,
    "\u2197": GlucoseTrend.RISING,
    "\u2191": GlucoseTrend.RISING,
    "\u21c8": GlucoseTrend.RISING_FAST,
    "\u2198": GlucoseTrend.FALLING,
    "\u2193": GlucoseTrend.FALLING,
    "\u21ca": GlucoseTrend.FALLING_FAST,
}


def parse_source_payload(payload: Any, *, kind: SourceKind) -> ParsedSourcePayload:
    rows = _extract_rows(payload)
    readings: list[SourceReading] = []
    issues: list[ImportIssue] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(
                ImportIssue(
                    row_number=index,
                    message="Source reading must be an object",
                    raw_record={"value": row},
                )
            )
            continue
        reading, issue = _parse_row(row, row_number=index, kind=kind)
        if issue is not None:
            issues.append(issue)
            continue
        if reading is not None:
            readings.append(reading)
    return ParsedSourcePayload(readings=readings, issues=issues)


def _extract_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("entries", "records", "sgv"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
        if any(field in payload for field in _VALUE_FIELDS):
            return [payload]
    return []


def _parse_row(
    row: dict[str, Any],
    *,
    row_number: int,
    kind: SourceKind,
) -> tuple[SourceReading | None, ImportIssue | None]:
    try:
        measured_at = _parse_timestamp(row)
        value = _parse_value(row)
        unit = _parse_unit(row, kind=kind)
    except ValueError as exc:
        return None, ImportIssue(
            row_number=row_number,
            message=str(exc),
            raw_record=dict(row),
        )
    return (
        SourceReading(
            measured_at=measured_at,
            value=value,
            unit=unit,
            trend=_parse_trend(row),
            device_id=_optional_text(row.get("device") or row.get("device_id")),
            source_record_id=_optional_text(row.get("_id") or row.get("recordId") or row.get("id")),
            raw_payload=dict(row),
        ),
        None,
    )


def _parse_timestamp(row: dict[str, Any]) -> datetime:
    for field in _TIME_FIELDS:
        value = row.get(field)
        if value in (None, ""):
            continue
        if field == "date" and isinstance(value, (int, float, str)):
            return _parse_epoch(value)
        return _parse_datetime_text(value)
    raise ValueError("Missing reading timestamp (date/dateString/systemTime)")


def _parse_epoch(value: Any) -> datetime:
    try:
        raw = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid epoch timestamp: {value}") from exc
    # Nightscout/xDrip `date` is normally epoch milliseconds. Accept seconds too
    # for test fixtures and bridge implementations that use Unix seconds.
    seconds = raw / 1000 if raw > 10_000_000_000 else raw
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0)


def _parse_datetime_text(value: Any) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("Empty timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid datetime timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _parse_value(row: dict[str, Any]) -> float:
    for field in _VALUE_FIELDS:
        value = row.get(field)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid glucose value: {value}") from exc
        if parsed <= 0:
            raise ValueError(f"Glucose value must be positive: {value}")
        return parsed
    raise ValueError("Missing glucose value (sgv/glucose/value)")


def _parse_unit(row: dict[str, Any], *, kind: SourceKind) -> GlucoseUnit:
    raw = row.get("unit") or row.get("units")
    if raw in (None, ""):
        # xDrip/Juggluco/Nightscout SGV feeds conventionally report mg/dL.
        return GlucoseUnit.MG_DL
    try:
        return parse_glucose_unit(raw)
    except ValueError:
        raise ValueError(f"Unsupported glucose unit from {kind}: {raw}") from None


def _parse_trend(row: dict[str, Any]) -> GlucoseTrend:
    raw = row.get("direction") or row.get("trend") or row.get("trend_arrow")
    if raw in (None, ""):
        return GlucoseTrend.UNKNOWN
    key = str(raw).strip().replace(" ", "").lower()
    return _TREND_MAP.get(key, GlucoseTrend.UNKNOWN)


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
