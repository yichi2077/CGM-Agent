from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str | None
    created_at: str
    updated_at: str
    hermes_resume_id: str | None
    hermes_continue_name: str | None
    message_count: int = 0


@dataclass(frozen=True)
class MessageRecord:
    id: str
    session_id: str
    role: str
    content: str
    created_at: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class AIOutputRecord:
    id: str
    session_id: str
    request_message_id: str
    response_message_id: str
    text: str
    raw_stdout: str
    raw_stderr: str
    returncode: int
    model: str | None
    provider: str | None
    toolsets: str | None
    skills: str | None
    created_at: str


class SQLiteStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    hermes_resume_id TEXT,
                    hermes_continue_name TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_outputs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    request_message_id TEXT NOT NULL,
                    response_message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_stdout TEXT NOT NULL,
                    raw_stderr TEXT NOT NULL,
                    returncode INTEGER NOT NULL,
                    model TEXT,
                    provider TEXT,
                    toolsets TEXT,
                    skills TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(request_message_id) REFERENCES messages(id) ON DELETE CASCADE,
                    FOREIGN KEY(response_message_id) REFERENCES messages(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    batch_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_format TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS raw_cgm_records (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_format TEXT NOT NULL,
                    row_number INTEGER,
                    recorded_at TEXT,
                    value REAL,
                    unit TEXT,
                    device_id TEXT,
                    source_record_id TEXT,
                    raw_payload_json TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES import_batches(batch_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_raw_cgm_records_batch
                    ON raw_cgm_records(batch_id);

                CREATE TABLE IF NOT EXISTS import_issues (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    row_number INTEGER,
                    field TEXT,
                    message TEXT NOT NULL,
                    raw_record_json TEXT,
                    FOREIGN KEY(batch_id) REFERENCES import_batches(batch_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_import_issues_batch
                    ON import_issues(batch_id);

                CREATE TABLE IF NOT EXISTS glucose_points (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    value_mg_dl REAL NOT NULL,
                    value_mmol_l REAL NOT NULL,
                    source TEXT NOT NULL,
                    quality_flag TEXT NOT NULL,
                    trend TEXT NOT NULL,
                    device_id TEXT,
                    session_id TEXT,
                    raw_record_id TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, timestamp, source)
                );

                CREATE INDEX IF NOT EXISTS idx_glucose_points_user_time
                    ON glucose_points(user_id, timestamp);

                CREATE TABLE IF NOT EXISTS device_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    sensor_started_at TEXT NOT NULL,
                    sensor_ended_at TEXT,
                    warmup_ended_at TEXT,
                    missing_ranges_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_device_sessions_user_start
                    ON device_sessions(user_id, sensor_started_at);

                CREATE TABLE IF NOT EXISTS user_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    ts_start TEXT NOT NULL,
                    ts_end TEXT,
                    payload_json TEXT NOT NULL,
                    attachment TEXT,
                    confidence REAL,
                    created_by TEXT NOT NULL,
                    user_confirmed INTEGER NOT NULL,
                    is_sensitive INTEGER NOT NULL,
                    is_rejected INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_user_events_user_start
                    ON user_events(user_id, ts_start);

                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    audience TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    report_anchor_time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sections_json TEXT NOT NULL,
                    rendered_markdown TEXT NOT NULL,
                    rendered_path TEXT,
                    evidence_refs_json TEXT NOT NULL,
                    data_quality_warnings_json TEXT NOT NULL,
                    g8_memory_candidates_json TEXT NOT NULL,
                    source_versions_json TEXT NOT NULL,
                    template_version TEXT NOT NULL DEFAULT 'g7-report-template-v1',
                    output_hash TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT 'reports.generate',
                    safety_result_json TEXT NOT NULL DEFAULT '{}',
                    audit_id TEXT,
                    generated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_reports_user_window
                    ON reports(user_id, window_start, window_end);

                CREATE TABLE IF NOT EXISTS l1_episodes (
                    episode_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    episode_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    source_report_id TEXT,
                    source_section_id TEXT,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    is_archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_referenced_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_l1_user_time
                    ON l1_episodes(user_id, occurred_at);

                CREATE TABLE IF NOT EXISTS l2_profile_items (
                    item_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    last_verified TEXT NOT NULL,
                    supersedes_item_id TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_l2_user_key
                    ON l2_profile_items(user_id, key);

                CREATE TABLE IF NOT EXISTS l3_hypotheses (
                    hypothesis_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'candidate',
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    contra_count INTEGER NOT NULL DEFAULT 0,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    last_checked TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_l3_user_state
                    ON l3_hypotheses(user_id, state);

                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    target_layer TEXT NOT NULL,
                    candidate_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    requires_user_confirmation INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    source_report_id TEXT,
                    source_section_id TEXT,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_candidates_user_status
                    ON memory_candidates(user_id, status);
                """
            )
            self._ensure_column(
                conn,
                "user_events",
                "is_rejected",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                conn,
                "reports",
                "generated_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "reports",
                "template_version",
                "TEXT NOT NULL DEFAULT 'g7-report-template-v1'",
            )
            self._ensure_column(
                conn,
                "reports",
                "output_hash",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "reports",
                "route",
                "TEXT NOT NULL DEFAULT 'reports.generate'",
            )
            self._ensure_column(
                conn,
                "reports",
                "safety_result_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

    def create_session(
        self,
        *,
        title: str | None = None,
        hermes_resume_id: str | None = None,
        hermes_continue_name: str | None = None,
    ) -> SessionRecord:
        session_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, title, created_at, updated_at, hermes_resume_id, hermes_continue_name
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    title,
                    now,
                    now,
                    hermes_resume_id,
                    hermes_continue_name,
                ),
            )
        return self.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        hermes_resume_id: str | None = None,
        hermes_continue_name: str | None = None,
    ) -> SessionRecord:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if hermes_resume_id is not None:
            fields.append("hermes_resume_id = ?")
            values.append(hermes_resume_id)
        if hermes_continue_name is not None:
            fields.append("hermes_continue_name = ?")
            values.append(hermes_continue_name)
        values.append(session_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?",
                values,
            )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       s.hermes_resume_id, s.hermes_continue_name,
                       COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return self._row_to_session(row)

    def list_sessions(self, *, limit: int = 50) -> list[SessionRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       s.hermes_resume_id, s.hermes_continue_name,
                       COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    def create_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        message_id = uuid.uuid4().hex
        now = utc_now()
        payload = json.dumps(metadata or {}, ensure_ascii=True, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (id, session_id, role, content, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, now, payload),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> MessageRecord:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, role, content, created_at, metadata_json
                FROM messages
                WHERE id = ?
                """,
                (message_id,),
            ).fetchone()
        if row is None:
            raise KeyError(message_id)
        return self._row_to_message(row)

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, created_at, metadata_json
                FROM messages
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def create_ai_output(
        self,
        *,
        session_id: str,
        request_message_id: str,
        response_message_id: str,
        text: str,
        raw_stdout: str,
        raw_stderr: str,
        returncode: int,
        model: str | None,
        provider: str | None,
        toolsets: str | None,
        skills: str | None,
    ) -> AIOutputRecord:
        output_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_outputs (
                    id, session_id, request_message_id, response_message_id,
                    text, raw_stdout, raw_stderr, returncode,
                    model, provider, toolsets, skills, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output_id,
                    session_id,
                    request_message_id,
                    response_message_id,
                    text,
                    raw_stdout,
                    raw_stderr,
                    returncode,
                    model,
                    provider,
                    toolsets,
                    skills,
                    now,
                ),
            )
        return self.get_ai_output(output_id)

    def get_ai_output(self, output_id: str) -> AIOutputRecord:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, session_id, request_message_id, response_message_id,
                       text, raw_stdout, raw_stderr, returncode,
                       model, provider, toolsets, skills, created_at
                FROM ai_outputs
                WHERE id = ?
                """,
                (output_id,),
            ).fetchone()
        if row is None:
            raise KeyError(output_id)
        return self._row_to_ai_output(row)

    def list_ai_outputs(self, session_id: str) -> list[AIOutputRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, request_message_id, response_message_id,
                       text, raw_stdout, raw_stderr, returncode,
                       model, provider, toolsets, skills, created_at
                FROM ai_outputs
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._row_to_ai_output(row) for row in rows]

    def create_audit_log(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        log_id = uuid.uuid4().hex
        now = utc_now()
        payload_json = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (id, session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (log_id, session_id, event_type, payload_json, now),
            )
        return log_id

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            hermes_resume_id=row["hermes_resume_id"],
            hermes_continue_name=row["hermes_continue_name"],
            message_count=int(row["message_count"] or 0),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    @staticmethod
    def _row_to_ai_output(row: sqlite3.Row) -> AIOutputRecord:
        return AIOutputRecord(
            id=row["id"],
            session_id=row["session_id"],
            request_message_id=row["request_message_id"],
            response_message_id=row["response_message_id"],
            text=row["text"],
            raw_stdout=row["raw_stdout"],
            raw_stderr=row["raw_stderr"],
            returncode=int(row["returncode"]),
            model=row["model"],
            provider=row["provider"],
            toolsets=row["toolsets"],
            skills=row["skills"],
            created_at=row["created_at"],
        )
