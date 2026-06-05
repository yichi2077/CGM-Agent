from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from hermes_cgm_agent.domain import (
    CreatedBy,
    EventType,
    GlucosePoint,
    GlucoseTrend,
    GlucoseUnit,
    QualityFlag,
    UserEvent,
    convert_glucose_value,
)
from hermes_cgm_agent.services.dexcom.config import DexcomConfig

# Dexcom v3 trend strings -> project GlucoseTrend. The project models five
# directional buckets plus UNKNOWN; Dexcom's 45°/single/double granularity folds
# onto them. ``none``/``notComputable``/``rateOutOfRange`` carry no usable
# direction.
_TREND_MAP: dict[str, GlucoseTrend] = {
    "doubleup": GlucoseTrend.RISING_FAST,
    "singleup": GlucoseTrend.RISING,
    "fortyfiveup": GlucoseTrend.RISING,
    "flat": GlucoseTrend.STABLE,
    "fortyfivedown": GlucoseTrend.FALLING,
    "singledown": GlucoseTrend.FALLING,
    "doubledown": GlucoseTrend.FALLING_FAST,
    "none": GlucoseTrend.UNKNOWN,
    "notcomputable": GlucoseTrend.UNKNOWN,
    "rateoutofrange": GlucoseTrend.UNKNOWN,
}

# Dexcom v3 eventType -> project EventType. ``bloodGlucose`` is a manual
# fingerstick (not a CGM reading) so it is recorded as a NOTE carrying the value.
_EVENT_TYPE_MAP: dict[str, EventType] = {
    "carbs": EventType.MEAL,
    "insulin": EventType.MEDICATION,
    "exercise": EventType.EXERCISE,
    "health": EventType.SYMPTOM,
    "bloodglucose": EventType.NOTE,
    "notes": EventType.NOTE,
    "note": EventType.NOTE,
}

# Physiological bounds Dexcom clamps to; readings at/over these (or flagged via
# ``status``) are kept but marked SUSPECT, matching the CSV normalizer policy.
_SUSPECT_LOW_MG_DL = 40.0
_SUSPECT_HIGH_MG_DL = 400.0


def parse_dexcom_datetime(value: str) -> datetime:
    """Parse a Dexcom systemTime/displayTime string to a UTC-aware datetime.

    systemTime is UTC by definition. Records sourced from mobile apps may carry
    an explicit UTC offset; receiver records are naive. Both are normalized to
    UTC so storage stays single-axis."""
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class DexcomMapper:
    def __init__(self, config: DexcomConfig) -> None:
        self.config = config

    # -- EGV -> GlucosePoint -------------------------------------------------

    def egv_to_point(self, record: dict[str, Any], *, user_id: str) -> GlucosePoint | None:
        """Map one Dexcom EGV record to a GlucosePoint.

        Returns ``None`` when the record has no usable glucose value (Dexcom may
        emit value=null for certain sensor states); the sync layer counts these
        as skipped rather than failing the batch."""
        value = _coerce_float(record.get("value"))
        system_time = record.get("systemTime")
        if value is None or value <= 0 or not system_time:
            return None

        unit = self._glucose_unit(record.get("unit"))
        timestamp = parse_dexcom_datetime(system_time)
        return GlucosePoint(
            user_id=user_id,
            timestamp=timestamp,
            value=value,
            unit=unit,
            source=self.config.source_label,
            quality_flag=self._quality_flag(value, unit, record.get("status")),
            trend=self._trend(record.get("trend")),
            device_id=record.get("transmitterId") or record.get("displayDevice"),
            raw_record_id=record.get("recordId"),
        )

    def _glucose_unit(self, raw_unit: Any) -> GlucoseUnit:
        if isinstance(raw_unit, str):
            try:
                return GlucoseUnit(raw_unit)
            except ValueError:
                pass
        return GlucoseUnit.MG_DL

    def _trend(self, raw_trend: Any) -> GlucoseTrend:
        if not isinstance(raw_trend, str):
            return GlucoseTrend.UNKNOWN
        return _TREND_MAP.get(raw_trend.strip().lower(), GlucoseTrend.UNKNOWN)

    def _quality_flag(self, value: float, unit: GlucoseUnit, status: Any) -> QualityFlag:
        if isinstance(status, str) and status.strip().lower() in {"low", "high"}:
            return QualityFlag.SUSPECT
        value_mg_dl = convert_glucose_value(value, unit, GlucoseUnit.MG_DL)
        if value_mg_dl < _SUSPECT_LOW_MG_DL or value_mg_dl > _SUSPECT_HIGH_MG_DL:
            return QualityFlag.SUSPECT
        return QualityFlag.VALID

    # -- Event -> UserEvent --------------------------------------------------

    def event_to_user_event(self, record: dict[str, Any], *, user_id: str) -> UserEvent | None:
        """Map one Dexcom event record to a UserEvent.

        Returns ``None`` for deleted records or records lacking an id/timestamp.
        Device-synced events are recorded as confirmed facts (created_by=device,
        user_confirmed=True) — they are not agent inferences."""
        if str(record.get("eventStatus", "")).strip().lower() == "deleted":
            return None
        record_id = record.get("recordId")
        system_time = record.get("systemTime")
        if not record_id or not system_time:
            return None

        raw_type = str(record.get("eventType", "")).strip().lower()
        event_type = _EVENT_TYPE_MAP.get(raw_type, EventType.NOTE)
        ts_start = parse_dexcom_datetime(system_time)
        payload = self._event_payload(record, raw_type)
        ts_end = self._event_end(ts_start, raw_type, record)

        return UserEvent(
            event_id=f"dexcom-evt-{record_id}",
            user_id=user_id,
            type=event_type,
            ts_start=ts_start,
            ts_end=ts_end,
            payload=payload,
            created_by=CreatedBy.DEVICE,
            user_confirmed=True,
        )

    def _event_payload(self, record: dict[str, Any], raw_type: str) -> dict[str, Any]:
        value = record.get("value")
        unit = record.get("unit")
        numeric = _coerce_float(value)
        payload: dict[str, Any] = {
            "source": "dexcom",
            "dexcom_event_type": record.get("eventType"),
            "dexcom_record_id": record.get("recordId"),
        }
        subtype = record.get("eventSubType")
        if subtype:
            payload["subtype"] = subtype
        if unit:
            payload["unit"] = unit
        if raw_type == "carbs" and numeric is not None:
            payload["carbs_grams"] = numeric
        elif raw_type == "insulin" and numeric is not None:
            payload["insulin_units"] = numeric
        elif raw_type == "exercise" and numeric is not None:
            payload["duration_minutes"] = numeric
        elif raw_type == "bloodglucose" and numeric is not None:
            payload["blood_glucose"] = numeric
        elif numeric is not None:
            payload["value"] = numeric
        elif value not in (None, ""):
            payload["value"] = value
        return payload

    def _event_end(self, ts_start: datetime, raw_type: str, record: dict[str, Any]) -> datetime | None:
        if raw_type != "exercise":
            return None
        if str(record.get("unit", "")).strip().lower() != "minutes":
            return None
        minutes = _coerce_float(record.get("value"))
        if minutes is None or minutes <= 0:
            return None
        return ts_start + timedelta(minutes=minutes)
