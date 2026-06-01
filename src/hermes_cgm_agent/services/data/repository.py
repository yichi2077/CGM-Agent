from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
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
                        _dt(record.recorded_at),
                        record.value,
                        record.unit,
                        record.device_id,
                        record.source_record_id,
                        _json(record.raw_payload),
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
                        _json_or_none(issue.raw_record),
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

    def create_glucose_point(self, point: GlucosePoint) -> str:
        point_id = uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO glucose_points (
                    id, user_id, timestamp, value, unit, value_mg_dl, value_mmol_l,
                    source, quality_flag, trend, device_id, session_id, raw_record_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    point_id,
                    point.user_id,
                    _dt(point.timestamp),
                    point.value,
                    point.unit,
                    point.value_mg_dl,
                    point.value_mmol_l,
                    point.source,
                    point.quality_flag,
                    point.trend,
                    point.device_id,
                    point.session_id,
                    point.raw_record_id,
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
                    session.device_id,
                    _dt(session.sensor_started_at),
                    _dt(session.sensor_ended_at),
                    _dt(session.warmup_ended_at),
                    _json([item.model_dump(mode="json") for item in session.missing_ranges]),
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

    def create_user_event(self, event: UserEvent) -> str:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_events (
                    event_id, user_id, type, ts_start, ts_end, payload_json,
                    attachment, confidence, created_by, user_confirmed,
                    is_sensitive, is_rejected, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.user_id,
                    event.event_type,
                    _dt(event.ts_start),
                    _dt(event.ts_end),
                    _json(event.payload),
                    event.attachment,
                    event.confidence,
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
        confirmed: bool,
        correction: dict[str, Any] | None = None,
    ) -> UserEvent:
        event = self.get_user_event(event_id, include_rejected=True)
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
                WHERE event_id = ?
                """,
                (
                    corrected.event_type,
                    _dt(corrected.ts_start),
                    _dt(corrected.ts_end),
                    _json(corrected.payload),
                    corrected.attachment,
                    corrected.confidence,
                    int(confirmed),
                    int(corrected.is_sensitive),
                    int(not confirmed),
                    event_id,
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

    @staticmethod
    def _row_to_raw_record(row: Any) -> RawCGMRecord:
        return RawCGMRecord(
            source_id=row["source_id"],
            source_format=row["source_format"],
            raw_payload=json.loads(row["raw_payload_json"]),
            row_number=row["row_number"],
            recorded_at=row["recorded_at"],
            value=row["value"],
            unit=row["unit"],
            device_id=row["device_id"],
            source_record_id=row["source_record_id"],
        )

    @staticmethod
    def _row_to_import_issue(row: Any) -> ImportIssue:
        raw_record = row["raw_record_json"]
        return ImportIssue(
            row_number=row["row_number"],
            field=row["field"],
            message=row["message"],
            raw_record=json.loads(raw_record) if raw_record else None,
        )

    @staticmethod
    def _row_to_glucose_point(row: Any) -> GlucosePoint:
        return GlucosePoint(
            user_id=row["user_id"],
            timestamp=row["timestamp"],
            value=row["value"],
            unit=row["unit"],
            source=row["source"],
            quality_flag=row["quality_flag"],
            trend=row["trend"],
            device_id=row["device_id"],
            session_id=row["session_id"],
            raw_record_id=row["raw_record_id"],
        )

    @staticmethod
    def _row_to_device_session(row: Any) -> DeviceSession:
        missing_ranges = [
            TimeRange.model_validate(item)
            for item in json.loads(row["missing_ranges_json"] or "[]")
        ]
        return DeviceSession(
            session_id=row["session_id"],
            user_id=row["user_id"],
            device_id=row["device_id"],
            sensor_started_at=row["sensor_started_at"],
            sensor_ended_at=row["sensor_ended_at"],
            warmup_ended_at=row["warmup_ended_at"],
            missing_ranges=missing_ranges,
        )

    @staticmethod
    def _row_to_user_event(row: Any) -> UserEvent:
        return UserEvent(
            event_id=row["event_id"],
            user_id=row["user_id"],
            type=row["type"],
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            payload=json.loads(row["payload_json"] or "{}"),
            attachment=row["attachment"],
            confidence=row["confidence"],
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
