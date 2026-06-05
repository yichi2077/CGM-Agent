from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hermes_cgm_agent.domain import (
    DataScope,
    DeviceSession,
    GlucosePoint,
    ImportIssue,
    RawCGMRecord,
    RawImportBatch,
    TimeRange,
    UserEvent,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore, utc_now

CGM_TABLES = [
    "import_batches",
    "raw_cgm_records",
    "import_issues",
    "glucose_points",
    "device_sessions",
    "user_events",
]


@dataclass(frozen=True)
class CGMRepositoryStatus:
    tables_present: bool
    table_count: int
    import_batch_count: int
    raw_record_count: int
    import_issue_count: int
    glucose_point_count: int
    device_session_count: int
    user_event_count: int


class SQLiteCGMRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def status(self) -> CGMRepositoryStatus:
        with self.store.connect() as conn:
            table_rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name IN ({})
                """.format(",".join("?" for _ in CGM_TABLES)),
                CGM_TABLES,
            ).fetchall()
            existing_tables = {row["name"] for row in table_rows}
            return CGMRepositoryStatus(
                tables_present=set(CGM_TABLES).issubset(existing_tables),
                table_count=len(existing_tables),
                import_batch_count=self._count(conn, "import_batches"),
                raw_record_count=self._count(conn, "raw_cgm_records"),
                import_issue_count=self._count(conn, "import_issues"),
                glucose_point_count=self._count(conn, "glucose_points"),
                device_session_count=self._count(conn, "device_sessions"),
                user_event_count=self._count(conn, "user_events"),
            )

    def create_import_batch(self, batch: RawImportBatch) -> RawImportBatch:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO import_batches (batch_id, source_name, source_format, imported_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    batch.batch_id,
                    batch.source_name,
                    batch.source_format,
                    _dt(batch.imported_at),
                ),
            )
            for record in batch.records:
                record_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO raw_cgm_records (
                        id, batch_id, source_id, source_format, row_number,
                        recorded_at, value, unit, device_id, source_record_id,
                        raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        batch.batch_id,
                        record.source_id,
                        record.source_format,
                        record.row_number,
                        self.store.seal(_dt_raw(record.recorded_at)),
                        self.store.seal(record.value),
                        self.store.seal(record.unit),
                        self.store.seal(record.device_id),
                        self.store.seal(record.source_record_id),
                        self.store.seal(record.raw_payload),
                    ),
                )
            for issue in batch.issues:
                issue_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO import_issues (
                        id, batch_id, row_number, field, message, raw_record_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue_id,
                        batch.batch_id,
                        issue.row_number,
                        issue.field,
                        issue.message,
                        self.store.seal(issue.raw_record) if issue.raw_record is not None else None,
                    ),
                )
        return self.get_import_batch(batch.batch_id)

    def get_import_batch(self, batch_id: str) -> RawImportBatch:
        with self.store.connect() as conn:
            batch_row = conn.execute(
                """
                SELECT batch_id, source_name, source_format, imported_at
                FROM import_batches
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            if batch_row is None:
                raise KeyError(batch_id)
            record_rows = conn.execute(
                """
                SELECT source_id, source_format, raw_payload_json, row_number,
                       recorded_at, value, unit, device_id, source_record_id
                FROM raw_cgm_records
                WHERE batch_id = ?
                ORDER BY row_number ASC, id ASC
                """,
                (batch_id,),
            ).fetchall()
            issue_rows = conn.execute(
                """
                SELECT row_number, field, message, raw_record_json
                FROM import_issues
                WHERE batch_id = ?
                ORDER BY row_number ASC, id ASC
                """,
                (batch_id,),
            ).fetchall()

        return RawImportBatch(
            batch_id=batch_row["batch_id"],
            source_name=batch_row["source_name"],
            source_format=batch_row["source_format"],
            imported_at=batch_row["imported_at"],
            records=[self._row_to_raw_record(row) for row in record_rows],
            issues=[self._row_to_import_issue(row) for row in issue_rows],
        )

    def create_glucose_point(self, point: GlucosePoint, *, replace: bool = False) -> str:
        # ``replace`` swaps plain INSERT for INSERT OR REPLACE so a forced re-sync
        # overwrites the row sharing the UNIQUE(user_id, timestamp, source) key
        # instead of raising IntegrityError. Default stays strict for dedup.
        verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
        point_id = uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute(
                f"""
                {verb} glucose_points (
                    id, user_id, timestamp, value, unit, value_mg_dl, value_mmol_l,
                    source, quality_flag, trend, device_id, session_id, raw_record_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    point_id,
                        point.user_id,
                        _dt(point.timestamp),
                        self.store.seal(point.value),
                        self.store.seal(point.unit),
                        self.store.seal(point.value_mg_dl),
                        self.store.seal(point.value_mmol_l),
                        point.source,
                        self.store.seal(point.quality_flag),
                        self.store.seal(point.trend),
                        self.store.seal(point.device_id),
                        self.store.seal(point.session_id),
                        self.store.seal(point.raw_record_id),
                        utc_now(),
                    ),
                )
        return point_id

    def list_glucose_points(self, scope: DataScope) -> list[GlucosePoint]:
        values: list[Any] = [
            scope.user_id,
            _dt(scope.window_start),
            _dt(scope.window_end),
        ]
        source_filter = ""
        if scope.source is not None:
            source_filter = "AND source = ?"
            values.append(scope.source)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT user_id, timestamp, value, unit, source, quality_flag,
                       trend, device_id, session_id, raw_record_id
                FROM glucose_points
                WHERE user_id = ?
                  AND timestamp >= ?
                  AND timestamp < ?
                  {source_filter}
                ORDER BY timestamp ASC, id ASC
                """,
                values,
            ).fetchall()
        return [self._row_to_glucose_point(row) for row in rows]

    def create_device_session(self, session: DeviceSession) -> str:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO device_sessions (
                    session_id, user_id, device_id, sensor_started_at,
                    sensor_ended_at, warmup_ended_at, missing_ranges_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.user_id,
                    self.store.seal(session.device_id),
                    _dt(session.sensor_started_at),
                    _dt(session.sensor_ended_at),
                    _dt(session.warmup_ended_at),
                    self.store.seal([item.model_dump(mode="json") for item in session.missing_ranges]),
                ),
            )
        return session.session_id

    def list_device_sessions(self, user_id: str) -> list[DeviceSession]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, user_id, device_id, sensor_started_at,
                       sensor_ended_at, warmup_ended_at, missing_ranges_json
                FROM device_sessions
                WHERE user_id = ?
                ORDER BY sensor_started_at ASC, session_id ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_device_session(row) for row in rows]

    def create_user_event(self, event: UserEvent, *, replace: bool = False) -> str:
        # ``replace`` overwrites the row sharing the event_id primary key, used by
        # a forced Dexcom re-sync (event_ids are deterministic: dexcom-evt-<id>).
        verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
        with self.store.connect() as conn:
            conn.execute(
                f"""
                {verb} user_events (
                    event_id, user_id, type, ts_start, ts_end, payload_json,
                    attachment, confidence, created_by, user_confirmed,
                    is_sensitive, is_rejected, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.user_id,
                    self.store.seal(event.event_type),
                    _dt(event.ts_start),
                    _dt(event.ts_end),
                    self.store.seal(event.payload),
                    self.store.seal(event.attachment),
                    self.store.seal(event.confidence),
                    event.created_by,
                    int(event.user_confirmed),
                    int(event.is_sensitive),
                    int(event.is_rejected),
                    utc_now(),
                ),
            )
        return event.event_id

    def get_user_event(self, event_id: str, *, include_rejected: bool = False) -> UserEvent:
        rejected_filter = ""
        if not include_rejected:
            rejected_filter = "AND is_rejected = 0"
        with self.store.connect() as conn:
            row = conn.execute(
                f"""
                SELECT event_id, user_id, type, ts_start, ts_end, payload_json,
                       attachment, confidence, created_by, user_confirmed,
                       is_sensitive, is_rejected
                FROM user_events
                WHERE event_id = ?
                {rejected_filter}
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._row_to_user_event(row)

    def confirm_user_event(
        self,
        event_id: str,
        *,
        user_id: str,
        confirmed: bool,
        correction: dict[str, Any] | None = None,
    ) -> UserEvent:
        event = self.get_user_event(event_id, include_rejected=True)
        # C2: enforce ownership. A caller may only confirm/reject/correct their
        # own event. A mismatch is reported as "not found" (no information leak).
        if event.user_id != user_id:
            raise KeyError(event_id)
        corrected = self._apply_event_correction(event, correction or {})
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE user_events
                SET type = ?,
                    ts_start = ?,
                    ts_end = ?,
                    payload_json = ?,
                    attachment = ?,
                    confidence = ?,
                    user_confirmed = ?,
                    is_sensitive = ?,
                    is_rejected = ?
                WHERE event_id = ? AND user_id = ?
                """,
                (
                    self.store.seal(corrected.event_type),
                    _dt(corrected.ts_start),
                    _dt(corrected.ts_end),
                    self.store.seal(corrected.payload),
                    self.store.seal(corrected.attachment),
                    self.store.seal(corrected.confidence),
                    int(confirmed),
                    int(corrected.is_sensitive),
                    int(not confirmed),
                    event_id,
                    user_id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(event_id)
        return self.get_user_event(event_id, include_rejected=True)

    def list_user_events(
        self,
        scope: DataScope,
        *,
        confirmed_only: bool = False,
        include_rejected: bool = False,
    ) -> list[UserEvent]:
        values: list[Any] = [
            scope.user_id,
            _dt(scope.window_start),
            _dt(scope.window_end),
        ]
        confirmed_filter = ""
        if confirmed_only:
            confirmed_filter = "AND user_confirmed = 1"
        rejected_filter = ""
        if not include_rejected:
            rejected_filter = "AND is_rejected = 0"
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, user_id, type, ts_start, ts_end, payload_json,
                       attachment, confidence, created_by, user_confirmed,
                       is_sensitive, is_rejected
                FROM user_events
                WHERE user_id = ?
                  AND ts_start >= ?
                  AND ts_start < ?
                  {confirmed_filter}
                  {rejected_filter}
                ORDER BY ts_start ASC, event_id ASC
                """,
                values,
            ).fetchall()
        return [self._row_to_user_event(row) for row in rows]

    @staticmethod
    def _count(conn: Any, table_name: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"])

    def _row_to_raw_record(self, row: Any) -> RawCGMRecord:
        return RawCGMRecord(
            source_id=row["source_id"],
            source_format=row["source_format"],
            raw_payload=self.store.unseal(row["raw_payload_json"], legacy="json"),
            row_number=row["row_number"],
            recorded_at=self.store.unseal(row["recorded_at"]),
            value=self.store.unseal(row["value"]),
            unit=self.store.unseal(row["unit"]),
            device_id=self.store.unseal(row["device_id"]),
            source_record_id=self.store.unseal(row["source_record_id"]),
        )

    def _row_to_import_issue(self, row: Any) -> ImportIssue:
        raw_record = row["raw_record_json"]
        return ImportIssue(
            row_number=row["row_number"],
            field=row["field"],
            message=row["message"],
            raw_record=self.store.unseal(raw_record, legacy="json") if raw_record else None,
        )

    def _row_to_glucose_point(self, row: Any) -> GlucosePoint:
        return GlucosePoint(
            user_id=row["user_id"],
            timestamp=row["timestamp"],
            value=self.store.unseal(row["value"]),
            unit=self.store.unseal(row["unit"]),
            source=row["source"],
            quality_flag=self.store.unseal(row["quality_flag"]),
            trend=self.store.unseal(row["trend"]),
            device_id=self.store.unseal(row["device_id"]),
            session_id=self.store.unseal(row["session_id"]),
            raw_record_id=self.store.unseal(row["raw_record_id"]),
        )

    def _row_to_device_session(self, row: Any) -> DeviceSession:
        missing_ranges = [
            TimeRange.model_validate(item)
            for item in self.store.unseal(row["missing_ranges_json"], legacy="json") or []
        ]
        return DeviceSession(
            session_id=row["session_id"],
            user_id=row["user_id"],
            device_id=self.store.unseal(row["device_id"]),
            sensor_started_at=row["sensor_started_at"],
            sensor_ended_at=row["sensor_ended_at"],
            warmup_ended_at=row["warmup_ended_at"],
            missing_ranges=missing_ranges,
        )

    def _row_to_user_event(self, row: Any) -> UserEvent:
        return UserEvent(
            event_id=row["event_id"],
            user_id=row["user_id"],
            type=self.store.unseal(row["type"]),
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            payload=self.store.unseal(row["payload_json"], legacy="json") or {},
            attachment=self.store.unseal(row["attachment"]),
            confidence=self.store.unseal(row["confidence"]),
            created_by=row["created_by"],
            user_confirmed=bool(row["user_confirmed"]),
            is_sensitive=bool(row["is_sensitive"]),
            is_rejected=bool(row["is_rejected"]),
        )

    @staticmethod
    def _apply_event_correction(event: UserEvent, correction: dict[str, Any]) -> UserEvent:
        if not correction:
            return event
        event_data = event.model_dump(by_alias=True)
        payload_patch = correction.get("payload")
        event_data.update(
            {
                key: value
                for key, value in correction.items()
                if key != "payload"
            }
        )
        if payload_patch is not None:
            event_data["payload"] = {
                **event.payload,
                **payload_patch,
            }
        return UserEvent.model_validate(event_data)


def _dt(value: datetime | str | None) -> str | None:
    """Serialize a timestamp to canonical UTC ISO (C6).

    Naive datetimes are assumed UTC; aware datetimes are converted to UTC. This
    is used for every range-queried fact AND for query bounds, so stored rows and
    bounds share one "+00:00" form and SQLite TEXT comparisons stay chronological
    (a naive stored row and a naive/aware bound can no longer mismatch). Use
    `_dt_raw` only for the raw import archive, where device-local naive values
    must be preserved verbatim for later re-normalization.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _dt_raw(value: datetime | str | None) -> str | None:
    """Faithful serialization for the raw import archive: preserve naive/offset
    exactly as the device reported it (normalization applies the configured
    source timezone later). Never used for range-queried tables."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_or_none(value: Any | None) -> str | None:
    if value is None:
        return None
    return _json(value)
