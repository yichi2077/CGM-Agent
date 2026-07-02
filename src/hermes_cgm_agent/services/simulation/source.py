from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import RawCGMRecord, RawImportBatch
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.data import CGMImporter


@dataclass(frozen=True)
class ReplayRecord:
    sim_ts: datetime
    record: RawCGMRecord
    reading_index: int


class StreamingSource(Protocol):
    batch: RawImportBatch

    def iter_records(self) -> Iterable[ReplayRecord]:
        ...


class CsvReplaySource:
    def __init__(
        self,
        path: str | Path,
        *,
        source_name: str | None = None,
        time_base: str = "original",
        days: int | None = None,
        default_timezone: str = "UTC",
        now: datetime | None = None,
        importer: CGMImporter | None = None,
    ) -> None:
        if time_base not in {"original", "shift-to-now"}:
            raise ValueError("time_base must be original or shift-to-now")
        self.path = Path(path)
        self.time_base = time_base
        self.days = days
        self.default_timezone = default_timezone
        self._now = now or utc_now()
        loaded = (importer or CGMImporter()).import_csv(
            self.path,
            source_name=source_name or self.path.name,
        )
        records = self._sorted_records(loaded.records)
        records = self._apply_days(records)
        records = self._apply_time_base(records)
        self.batch = loaded.model_copy(update={"records": records})

    def iter_records(self) -> Iterable[ReplayRecord]:
        for index, record in enumerate(self.batch.records, start=1):
            assert record.recorded_at is not None
            yield ReplayRecord(
                sim_ts=self._to_utc(record.recorded_at),
                record=record,
                reading_index=index,
            )

    def _sorted_records(self, records: list[RawCGMRecord]) -> list[RawCGMRecord]:
        return sorted(
            records,
            key=lambda record: self._to_utc(record.recorded_at)
            if record.recorded_at is not None
            else datetime.max.replace(tzinfo=timezone.utc),
        )

    def _apply_days(self, records: list[RawCGMRecord]) -> list[RawCGMRecord]:
        if self.days is None or not records:
            return records
        first = self._to_utc(records[0].recorded_at)
        cutoff = first + timedelta(days=self.days)
        return [
            record
            for record in records
            if record.recorded_at is not None and self._to_utc(record.recorded_at) < cutoff
        ]

    def _apply_time_base(self, records: list[RawCGMRecord]) -> list[RawCGMRecord]:
        if self.time_base == "original" or not records:
            return records
        latest = self._to_utc(records[-1].recorded_at)
        delta = self._now.astimezone(timezone.utc).replace(microsecond=0) - latest
        return [
            record.model_copy(update={"recorded_at": record.recorded_at + delta})
            if record.recorded_at is not None
            else record
            for record in records
        ]

    def _to_utc(self, value: datetime | None) -> datetime:
        if value is None:
            raise ValueError("recorded_at is required")
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(self.default_timezone))
        return value.astimezone(timezone.utc).replace(microsecond=0)
