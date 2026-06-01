from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from hermes_cgm_agent.domain import (
    GlucoseUnit,
    ImportIssue,
    RawCGMRecord,
    RawImportBatch,
)


@dataclass(frozen=True)
class FieldMapping:
    timestamp: str = "timestamp"
    value: str = "value"
    unit: str = "unit"
    device_id: str | None = "device_id"
    source_record_id: str | None = "record_id"
    default_unit: GlucoseUnit | str | None = None


@dataclass(frozen=True)
class ImportReport:
    batch_id: str
    source_name: str
    source_format: str
    record_count: int
    issue_count: int
    issue_fields: tuple[str, ...]

    @property
    def has_issues(self) -> bool:
        return self.issue_count > 0


class CGMImporter:
    def __init__(self, mapping: FieldMapping | None = None) -> None:
        self.mapping = mapping or FieldMapping()

    def import_file(
        self,
        path: str | Path,
        *,
        batch_id: str | None = None,
        source_name: str | None = None,
    ) -> RawImportBatch:
        source_path = Path(path)
        suffix = source_path.suffix.lower()
        if suffix == ".csv":
            return self.import_csv(source_path, batch_id=batch_id, source_name=source_name)
        if suffix == ".json":
            return self.import_json(source_path, batch_id=batch_id, source_name=source_name)
        raise ValueError(f"Unsupported CGM import file type: {source_path.suffix}")

    def import_csv(
        self,
        path: str | Path,
        *,
        batch_id: str | None = None,
        source_name: str | None = None,
    ) -> RawImportBatch:
        source_path = Path(path)
        records: list[RawCGMRecord] = []
        issues: list[ImportIssue] = []
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                issues.append(
                    ImportIssue(
                        message="CSV file is empty or missing a header row",
                        raw_record={"path": str(source_path)},
                    )
                )
            else:
                for row_number, row in enumerate(reader, start=2):
                    self._append_row_result(
                        source_id=str(source_path),
                        source_format="csv",
                        row=row,
                        row_number=row_number,
                        records=records,
                        issues=issues,
                    )
        return RawImportBatch(
            batch_id=batch_id or uuid.uuid4().hex,
            source_name=source_name or source_path.name,
            source_format="csv",
            records=records,
            issues=issues,
        )

    def import_json(
        self,
        path: str | Path,
        *,
        batch_id: str | None = None,
        source_name: str | None = None,
    ) -> RawImportBatch:
        source_path = Path(path)
        records: list[RawCGMRecord] = []
        issues: list[ImportIssue] = []
        with source_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rows = self._extract_json_rows(payload)
        if rows is None:
            issues.append(
                ImportIssue(
                    message="JSON import expects an array or an object with a records array",
                    raw_record={"path": str(source_path)},
                )
            )
        else:
            for row_number, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    issues.append(
                        ImportIssue(
                            row_number=row_number,
                            message="JSON record must be an object",
                            raw_record={"value": row},
                        )
                    )
                    continue
                self._append_row_result(
                    source_id=str(source_path),
                    source_format="json",
                    row=row,
                    row_number=row_number,
                    records=records,
                    issues=issues,
                )
        return RawImportBatch(
            batch_id=batch_id or uuid.uuid4().hex,
            source_name=source_name or source_path.name,
            source_format="json",
            records=records,
            issues=issues,
        )

    @staticmethod
    def build_report(batch: RawImportBatch) -> ImportReport:
        issue_fields = sorted(
            {
                issue.field
                for issue in batch.issues
                if issue.field is not None
            }
        )
        return ImportReport(
            batch_id=batch.batch_id,
            source_name=batch.source_name,
            source_format=str(batch.source_format),
            record_count=batch.record_count,
            issue_count=batch.issue_count,
            issue_fields=tuple(issue_fields),
        )

    def _append_row_result(
        self,
        *,
        source_id: str,
        source_format: str,
        row: dict[str, Any],
        row_number: int,
        records: list[RawCGMRecord],
        issues: list[ImportIssue],
    ) -> None:
        row_issues = self._validate_row(row, row_number)
        if row_issues:
            issues.extend(row_issues)
            return
        try:
            records.append(
                RawCGMRecord(
                    source_id=source_id,
                    source_format=source_format,
                    raw_payload=dict(row),
                    row_number=row_number,
                    recorded_at=_parse_datetime(_required(row, self.mapping.timestamp)),
                    value=_parse_value(_required(row, self.mapping.value)),
                    unit=self._parse_unit(row),
                    device_id=_optional(row, self.mapping.device_id),
                    source_record_id=_optional(row, self.mapping.source_record_id),
                )
            )
        except (ValueError, ValidationError) as exc:
            issues.append(
                ImportIssue(
                    row_number=row_number,
                    message=str(exc),
                    raw_record=dict(row),
                )
            )

    def _validate_row(self, row: dict[str, Any], row_number: int) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        for field_name in [self.mapping.timestamp, self.mapping.value]:
            if _is_blank(row.get(field_name)):
                issues.append(
                    ImportIssue(
                        row_number=row_number,
                        field=field_name,
                        message=f"Missing required field: {field_name}",
                        raw_record=dict(row),
                    )
                )
        if self.mapping.default_unit is None and _is_blank(row.get(self.mapping.unit)):
            issues.append(
                ImportIssue(
                    row_number=row_number,
                    field=self.mapping.unit,
                    message=f"Missing required field: {self.mapping.unit}",
                    raw_record=dict(row),
                )
            )
        if issues:
            return issues
        try:
            _parse_datetime(_required(row, self.mapping.timestamp))
        except ValueError as exc:
            issues.append(
                ImportIssue(
                    row_number=row_number,
                    field=self.mapping.timestamp,
                    message=str(exc),
                    raw_record=dict(row),
                )
            )
        try:
            _parse_value(_required(row, self.mapping.value))
        except ValueError as exc:
            issues.append(
                ImportIssue(
                    row_number=row_number,
                    field=self.mapping.value,
                    message=str(exc),
                    raw_record=dict(row),
                )
            )
        try:
            self._parse_unit(row)
        except ValueError as exc:
            issues.append(
                ImportIssue(
                    row_number=row_number,
                    field=self.mapping.unit,
                    message=str(exc),
                    raw_record=dict(row),
                )
            )
        return issues

    def _parse_unit(self, row: dict[str, Any]) -> GlucoseUnit:
        raw_unit = row.get(self.mapping.unit)
        if _is_blank(raw_unit):
            raw_unit = self.mapping.default_unit
        try:
            return GlucoseUnit(str(raw_unit))
        except ValueError as exc:
            raise ValueError(f"Unsupported glucose unit: {raw_unit}") from exc

    @staticmethod
    def _extract_json_rows(payload: Any) -> Iterable[Any] | None:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            return payload["records"]
        return None


def _required(row: dict[str, Any], field_name: str) -> Any:
    value = row.get(field_name)
    if _is_blank(value):
        raise ValueError(f"Missing required field: {field_name}")
    return value


def _optional(row: dict[str, Any], field_name: str | None) -> str | None:
    if field_name is None:
        return None
    value = row.get(field_name)
    if _is_blank(value):
        return None
    return str(value)


def _parse_datetime(value: Any) -> datetime:
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    # IMPORTANT (C1): do NOT attach UTC to naive timestamps here. A device export
    # without an offset is local time; normalization applies the configured
    # source timezone (NormalizationConfig.default_timezone) for naive values.
    # Forcing UTC here would make that branch dead code and shift local data.
    return parsed


def _parse_value(value: Any) -> float:
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"Invalid glucose value: {value}") from exc
    if parsed <= 0:
        raise ValueError(f"Glucose value must be positive: {value}")
    return parsed


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""
