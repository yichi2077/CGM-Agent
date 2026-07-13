from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hermes_cgm_agent.domain import GlucosePoint, GlucoseTrend, GlucoseUnit, QualityFlag
from hermes_cgm_agent.services.aidex.config import AidexConfig


def parse_aidex_datetime(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise ValueError("AiDEX record is missing appTime")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # The official resource contract defines all returned times as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


class AidexMapper:
    def __init__(self, config: AidexConfig) -> None:
        self.config = config

    def sensor_glucose_to_point(
        self,
        record: dict[str, Any],
        *,
        user_id: str,
        received_at: datetime,
    ) -> GlucosePoint | None:
        try:
            value = float(record.get("glucose"))
        except (TypeError, ValueError):
            return None
        if value <= 0 or not record.get("appTime"):
            return None
        measured_at = parse_aidex_datetime(record["appTime"])
        serial = str(record.get("sn") or "").strip() or None
        record_id = f"{serial or 'unknown'}:{measured_at.isoformat()}"
        return GlucosePoint(
            user_id=user_id,
            timestamp=measured_at,
            received_at=received_at,
            value=value,
            unit=GlucoseUnit.MG_DL,
            source=self.config.source_label,
            quality_flag=self._quality_flag(record, value),
            trend=GlucoseTrend.UNKNOWN,
            device_id=serial,
            raw_record_id=record_id,
        )

    @staticmethod
    def _quality_flag(record: dict[str, Any], value: float) -> QualityFlag:
        if record.get("eventWarning") == -1:
            return QualityFlag.WARMUP
        try:
            status = int(record.get("status", 0))
        except (TypeError, ValueError):
            status = 1
        if status != 0 or value < 36 or value > 450:
            return QualityFlag.SUSPECT
        return QualityFlag.VALID
