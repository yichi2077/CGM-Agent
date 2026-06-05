from __future__ import annotations

import tempfile
import unittest
import sqlite3
from datetime import datetime, time, timezone
from pathlib import Path

from hermes_cgm_agent.domain import DataScope, EvidenceRef, L1Episode, UserEvent
from hermes_cgm_agent.domain.report import Report, ReportSection
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import SQLiteReportRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class StorageEncryptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "app.db"
        self.store = SQLiteStore(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sensitive_health_payloads_are_not_plaintext_in_sqlite_file(self) -> None:
        cgm = SQLiteCGMRepository(self.store)
        memory = SQLiteMemoryRepository(self.store)
        reports = SQLiteReportRepository(self.store)
        audit = AuditService(self.store)

        cgm.create_user_event(
            UserEvent(
                event_id="event-secret",
                user_id="user-1",
                type="meal",
                ts_start=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                payload={"note": "secret-breakfast-payload"},
                attachment="secret-event-attachment",
                created_by="user",
                user_confirmed=True,
            )
        )
        memory.create_episode(
            L1Episode(
                episode_id="episode-secret",
                user_id="user-1",
                occurred_at=datetime(2026, 5, 31, 8, 0, tzinfo=timezone.utc),
                episode_type="meal",
                summary="secret-memory-summary",
                payload={"note": "secret-memory-payload"},
            )
        )
        reports.create_report(
            Report(
                report_id="report-secret",
                user_id="user-1",
                report_type="daily",
                audience="self",
                data_scope=DataScope(
                    user_id="user-1",
                    window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                    window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
                ),
                timezone="Asia/Shanghai",
                report_anchor_time=time(7, 0),
                rendered_markdown="secret-report-markdown",
                sections=[
                    ReportSection(
                        section_id="secret-section",
                        kind="text",
                        title="Private",
                        content="secret-report-section",
                        data_scope=DataScope(
                            user_id="user-1",
                            window_start=datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc),
                            window_end=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
                        ),
                        evidence_refs=[EvidenceRef(kind="event", ref_id="event-secret")],
                    )
                ],
            )
        )
        audit.log("session-secret", "test.event", {"note": "secret-audit-payload"})

        raw = self.db_path.read_bytes()
        for secret in [
            b"secret-breakfast-payload",
            b"secret-event-attachment",
            b"secret-memory-summary",
            b"secret-memory-payload",
            b"secret-report-markdown",
            b"secret-report-section",
            b"secret-audit-payload",
        ]:
            self.assertNotIn(secret, raw)

        event = cgm.get_user_event("event-secret")
        episode = memory.get_episode("episode-secret")
        report = reports.get_report("report-secret")
        self.assertEqual(event.payload["note"], "secret-breakfast-payload")
        self.assertEqual(episode.summary, "secret-memory-summary")
        self.assertEqual(report.rendered_markdown, "secret-report-markdown")

    def test_initialize_migrates_legacy_local_session_tables(self) -> None:
        legacy_db = Path(self.temp_dir.name) / "legacy.db"
        conn = sqlite3.connect(legacy_db)
        try:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    hermes_resume_id TEXT,
                    hermes_continue_name TEXT
                );
                CREATE TABLE messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE ai_outputs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    request_message_id TEXT NOT NULL,
                    response_message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    raw_stdout TEXT NOT NULL,
                    raw_stderr TEXT NOT NULL,
                    returncode INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE audit_logs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                INSERT INTO sessions (id, title, created_at, updated_at)
                VALUES ('legacy-session', 'Legacy', '2026-06-05T00:00:00+00:00', '2026-06-05T00:00:00+00:00');
                INSERT INTO audit_logs (id, session_id, event_type, payload_json, created_at)
                VALUES ('legacy-audit', 'legacy-session', 'legacy.event', '{}', '2026-06-05T00:00:00+00:00');
                """
            )
            conn.commit()
        finally:
            conn.close()

        store = SQLiteStore(legacy_db)
        store.initialize()
        AuditService(store).log("hermes-session", "new.event", {"status": "ok"})

        with store.connect() as migrated:
            tables = {
                row["name"]
                for row in migrated.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            audit_fks = migrated.execute("PRAGMA foreign_key_list(audit_logs)").fetchall()

        self.assertNotIn("sessions", tables)
        self.assertNotIn("messages", tables)
        self.assertNotIn("ai_outputs", tables)
        self.assertEqual(audit_fks, [])


if __name__ == "__main__":
    unittest.main()
