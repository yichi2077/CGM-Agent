from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import local
from threading import Lock
from typing import Any, Iterator, Literal

from cryptography.fernet import Fernet, InvalidToken


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_ACL_HARDENED: set[str] = set()
_ACL_LOCK = Lock()
_WINDOWS_IDENTITY: str | None = None


def _harden_windows_acl(path: Path) -> None:
    """M-19: restrict file permissions on Windows using icacls.

    Removes inherited ACL entries and grants full control only to the
    current user, mirroring the ``0o600`` chmod on POSIX. Wrapped in
    try/except so a failure never blocks database initialization.
    """
    global _WINDOWS_IDENTITY
    user = _WINDOWS_IDENTITY
    if not user:
        # ``USERNAME`` can be inherited from a desktop session while the
        # process actually runs under a service/sandbox identity.  Grant the
        # SID returned by ``whoami`` first so the process that opened SQLite
        # keeps access to the file it just hardened.
        try:
            identity = subprocess.run(
                ["whoami"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            identity = ""
        user = identity or os.environ.get("USERNAME", "")
        _WINDOWS_IDENTITY = user
    if not user:
        return
    key = str(path.resolve()).casefold()
    with _ACL_LOCK:
        if key in _ACL_HARDENED:
            return
        # Mark before spawning the child so concurrent SQLite connections do
        # not all launch an identical icacls process. A later failure removes
        # the marker and may retry once on the next connection.
        _ACL_HARDENED.add(key)
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        with _ACL_LOCK:
            _ACL_HARDENED.discard(key)


# M-20: whitelist of table names allowed in _ensure_column's f-string SQL.
# This prevents SQL injection through the table_name parameter, which is
# interpolated into PRAGMA/ALTER TABLE statements (SQLite does not accept
# parameterized identifiers).
_WHITELISTED_TABLES = frozenset({
    "audit_logs",
    "import_batches",
    "raw_cgm_records",
    "import_issues",
    "glucose_points",
    "detected_glucose_events",
    "device_sessions",
    "user_events",
    "reports",
    "l1_episodes",
    "l2_profile_items",
    "l3_hypotheses",
    "memory_candidates",
    "memory_summaries",
    "push_events",
    "pending_interactions",
    "unread_badges",
    "safety_red_zone_events",
})


class _StorageCipher:
    PREFIX = "enc:v1:"

    def __init__(self, key_path: Path, env_key: str | None = None) -> None:
        self.key_path = key_path
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = env_key.encode("utf-8") if env_key else self._load_or_create_key()
        self._fernet = Fernet(key)
        self._harden_permissions()

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            # Validate eagerly: an empty or corrupted key file would otherwise
            # surface as a bare "Fernet key must be 32 url-safe base64-encoded
            # bytes" with no hint that it is THIS file that is broken.
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"Storage key file {self.key_path} is empty or corrupted; "
                    "it must contain a Fernet key (32 url-safe base64 bytes). "
                    "If the original key is lost, previously encrypted data "
                    "cannot be recovered — restore the key from backup before "
                    "touching the database."
                ) from exc
            return key
        key = Fernet.generate_key()
        self.key_path.write_bytes(key + b"\n")
        return key

    def _harden_permissions(self) -> None:
        if os.name == "nt":
            _harden_windows_acl(self.key_path)
            return
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            return

    def seal(self, value: Any) -> str | None:
        if value is None:
            return None
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
        return f"{self.PREFIX}{self._fernet.encrypt(payload).decode('utf-8')}"

    def open(self, value: Any, *, legacy: Literal["raw", "json"] = "raw") -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(self.PREFIX):
            token = value[len(self.PREFIX) :].encode("utf-8")
            try:
                payload = self._fernet.decrypt(token).decode("utf-8")
            except InvalidToken as exc:
                # Surface an explicit, actionable error instead of leaking a silent
                # None / partial read (F1 edge case). A correctly located store keeps
                # its key beside it; a mismatch usually means the DB and key were
                # separated (see config storage_key_path / CGM_AGENT_STORAGE_KEY).
                raise RuntimeError(
                    "Failed to decrypt a stored value with the current storage key; "
                    "the database and its Fernet key appear mismatched or separated."
                ) from exc
            return json.loads(payload)
        if legacy == "json" and isinstance(value, str):
            return json.loads(value)
        return value


class SQLiteStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        env_key_path = os.getenv("CGM_AGENT_STORAGE_KEY_PATH")
        env_key = os.getenv("CGM_AGENT_STORAGE_KEY")
        # H-12: warn when the Fernet key is passed via environment variable,
        # as it is visible in the process environment (e.g. /proc/PID/environ).
        if env_key:
            import logging
            logging.getLogger("hermes_cgm_agent.storage").warning(
                "CGM_AGENT_STORAGE_KEY is set; the Fernet key is visible in the "
                "process environment. Prefer CGM_AGENT_STORAGE_KEY_PATH for "
                "production deployments."
            )
        key_path = Path(env_key_path).expanduser() if env_key_path else self.db_path.parent / "storage.key"
        self._cipher = _StorageCipher(key_path, env_key=env_key)
        # B3: active transaction state is local to the calling thread.  A
        # shared instance attribute would hand one thread's SQLite connection
        # to another thread while a transaction is open, which SQLite rejects.
        self._transaction_state = local()
        self._harden_permissions()

    def _active_transaction_conn(self) -> sqlite3.Connection | None:
        return getattr(self._transaction_state, "conn", None)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # B3: if a transaction is active, reuse its connection so all writes
        # participate in the same atomic commit/rollback. This lets existing
        # repository methods join a transaction without signature changes.
        active_conn = self._active_transaction_conn()
        if active_conn is not None:
            yield active_conn
            return
        # WAL + a generous busy timeout: the CLI (cron push-tick, source-poll)
        # and the Hermes plugin process share this one database file, so
        # writer/writer overlap is a normal operating condition, not an error.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # L-18: WAL mode is set once in initialize(); not needed per-connection.
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
            self._harden_permissions()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """B3: atomic transaction — all writes within the block share one
        connection and commit/rollback together.

        While active, :meth:`connect` yields this connection instead of
        creating a new one, so existing repository methods participate in the
        transaction without signature changes. Nested calls reuse the outer
        transaction.
        """
        active_conn = self._active_transaction_conn()
        if active_conn is not None:
            # Nested transaction: isolate the inner unit with a savepoint.  If
            # its exception is caught by the outer caller, its writes still
            # roll back instead of being committed with the outer transaction.
            depth = getattr(self._transaction_state, "depth", 0) + 1
            savepoint = f"cgm_tx_{depth}"
            active_conn.execute(f"SAVEPOINT {savepoint}")
            self._transaction_state.depth = depth
            try:
                yield active_conn
                active_conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                active_conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                active_conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            finally:
                self._transaction_state.depth = depth - 1
            return
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # L-18: WAL mode is set once in initialize(); not needed per-connection.
        self._transaction_state.conn = conn
        self._transaction_state.depth = 0
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            del self._transaction_state.conn
            del self._transaction_state.depth
            conn.close()
            self._harden_permissions()

    def initialize(self) -> None:
        with self.connect() as conn:
            # L-18: set WAL mode once here; it is a database-level persistent
            # setting that survives across connections, so per-connection
            # PRAGMA calls in connect()/transaction() are redundant.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                -- L-24: add indexes for common audit query patterns.
                CREATE INDEX IF NOT EXISTS idx_audit_logs_session ON audit_logs(session_id);
                CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type);

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
                    received_at TEXT,
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

                CREATE TABLE IF NOT EXISTS detected_glucose_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    ts_start TEXT NOT NULL,
                    ts_end TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    peak_value_mg_dl REAL,
                    nadir_value_mg_dl REAL,
                    duration_minutes REAL NOT NULL,
                    point_count INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    detector_version TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_detected_events_user_start
                    ON detected_glucose_events(user_id, ts_start);

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
                    valid_from TEXT,
                    valid_to TEXT,
                    source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
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
                    category TEXT NOT NULL DEFAULT 'behavioral',
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    contra_count INTEGER NOT NULL DEFAULT 0,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    valid_from TEXT,
                    valid_to TEXT,
                    source_episode_ids_json TEXT NOT NULL DEFAULT '[]',
                    contra_episode_ids_json TEXT NOT NULL DEFAULT '[]',
                    supersedes_hypothesis_id TEXT,
                    last_checked TEXT NOT NULL,
                    last_evidence_added TEXT,
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

                CREATE TABLE IF NOT EXISTS memory_summaries (
                    summary_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_summaries_user
                    ON memory_summaries(user_id, created_at);

                -- P2 tiered-push state: scheduling metadata only (no PHI). The
                -- UNIQUE(user_id, tier, period_key) constraint makes a push
                -- idempotent within its period (daily/weekly/monthly).
                CREATE TABLE IF NOT EXISTS push_events (
                    push_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    summary_id TEXT,
                    delivery_id TEXT,
                    pushed_at TEXT NOT NULL,
                    UNIQUE(user_id, tier, period_key)
                );

                CREATE INDEX IF NOT EXISTS idx_push_events_user
                    ON push_events(user_id, tier, period_key);

                -- P1-6 (MVP audit): per-(user, local-day) idempotency ledger for
                -- scheduler-driven staged consolidation (push_tick closes the
                -- L1->L2->L3 loop even when sessions never end gracefully).
                CREATE TABLE IF NOT EXISTS consolidation_runs (
                    user_id TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    ran_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, day_key)
                );

                -- F4 pending interaction tracking (with TTL)
                CREATE TABLE IF NOT EXISTS pending_interactions (
                    interaction_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    interaction_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_pending_interactions_user
                    ON pending_interactions(user_id, is_active);

                -- F4 OS push failure fallback state
                CREATE TABLE IF NOT EXISTS unread_badges (
                    user_id TEXT PRIMARY KEY,
                    badge_count INTEGER NOT NULL DEFAULT 0
                );

                -- TD2: red-zone recovery state persistence (F3-B3).
                -- Survives process restart so the 2-hour recovery double-check
                -- window is not lost.  One row per user (PRIMARY KEY).
                CREATE TABLE IF NOT EXISTS safety_red_zone_events (
                    user_id TEXT PRIMARY KEY,
                    triggered_at TEXT NOT NULL,
                    safety_result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # A3: track the ORIGINAL trigger time separately from the renewed
            # triggered_at so the recovery double-check baseline is preserved
            # across window renewals (long red-zone > 2 h).
            self._ensure_column(
                conn,
                "safety_red_zone_events",
                "original_triggered_at",
                "TEXT",
            )
            # Pre-A3 rows have no separate original timestamp.  Their existing
            # trigger is the only truthful baseline and must survive renewal.
            conn.execute(
                "UPDATE safety_red_zone_events "
                "SET original_triggered_at = triggered_at "
                "WHERE original_triggered_at IS NULL"
            )
            self._ensure_column(
                conn,
                "glucose_points",
                "received_at",
                "TEXT",
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
            # D032: bi-temporal validity + lineage on L2/L3 (migrate existing DBs).
            for table in ("l2_profile_items", "l3_hypotheses"):
                self._ensure_column(conn, table, "valid_from", "TEXT")
                self._ensure_column(conn, table, "valid_to", "TEXT")
                self._ensure_column(
                    conn, table, "source_episode_ids_json", "TEXT NOT NULL DEFAULT '[]'"
                )
            self._ensure_column(
                conn, "l3_hypotheses", "contra_episode_ids_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column(
                conn, "l3_hypotheses", "supersedes_hypothesis_id", "TEXT"
            )
            # C-02: track last-evidence-added time for decay calculation.
            self._ensure_column(
                conn, "l3_hypotheses", "last_evidence_added", "TEXT"
            )
            # P1-4 (MVP audit): hypothesis content class; silent consent only
            # auto-advances 'behavioral' rows. Existing rows default to
            # 'behavioral' (all historical hypotheses came from the behavioral
            # pattern miner).
            self._ensure_column(
                conn, "l3_hypotheses", "category", "TEXT NOT NULL DEFAULT 'behavioral'"
            )
            # B4: candidate queue TTL — pending candidates expire after 30 days
            # so unconfirmed entries don't accumulate indefinitely.
            self._ensure_column(
                conn,
                "memory_candidates",
                "expires_at",
                "TEXT",
            )
            self._backfill_candidate_expiry(conn)
            self._migrate_legacy_session_tables(conn)
        self._harden_permissions()

    def _harden_permissions(self) -> None:
        # WAL mode adds -wal/-shm sidecar files that carry the same PHI as the
        # main database; they must be locked down alongside it.
        suffixes = ("", "-wal", "-shm")
        for suffix in suffixes:
            path = Path(f"{self.db_path}{suffix}")
            if path.exists():
                if os.name == "nt":
                    # M-19: Windows ACL hardening using icacls.
                    _harden_windows_acl(path)
                else:
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass

    def seal(self, value: Any) -> str | None:
        return self._cipher.seal(value)

    def unseal(self, value: Any, *, legacy: Literal["raw", "json"] = "raw") -> Any:
        return self._cipher.open(value, legacy=legacy)

    def create_audit_log(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        log_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (id, session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (log_id, session_id, event_type, self.seal(payload), utc_now()),
            )
        return log_id

    @staticmethod
    def _backfill_candidate_expiry(conn: sqlite3.Connection) -> None:
        """Give pre-B4 pending candidates the same 30-day TTL as new rows."""
        rows = conn.execute(
            "SELECT candidate_id, created_at FROM memory_candidates "
            "WHERE status = 'pending' AND expires_at IS NULL"
        ).fetchall()
        for row in rows:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                expires_at = (created_at + timedelta(days=30)).isoformat()
            except (TypeError, ValueError):
                # A malformed legacy timestamp cannot safely remain pending.
                expires_at = utc_now()
            conn.execute(
                "UPDATE memory_candidates SET expires_at = ? WHERE candidate_id = ?",
                (expires_at, row["candidate_id"]),
            )

    @staticmethod
    def _migrate_legacy_session_tables(conn: sqlite3.Connection) -> None:
        audit_fks = conn.execute("PRAGMA foreign_key_list(audit_logs)").fetchall()
        if any(row["table"] == "sessions" for row in audit_fks):
            conn.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")
            conn.execute(
                """
                CREATE TABLE audit_logs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO audit_logs (id, session_id, event_type, payload_json, created_at)
                SELECT id, session_id, event_type, payload_json, created_at
                FROM audit_logs_legacy
                """
            )
            conn.execute("DROP TABLE audit_logs_legacy")

        conn.execute("DROP TABLE IF EXISTS ai_outputs")
        conn.execute("DROP TABLE IF EXISTS messages")
        conn.execute("DROP TABLE IF EXISTS sessions")

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        # M-20: validate table_name against a whitelist before interpolating
        # it into SQL (SQLite does not accept parameterized identifiers).
        if table_name not in _WHITELISTED_TABLES:
            raise ValueError(f"Unknown table name in _ensure_column: {table_name}")
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )
