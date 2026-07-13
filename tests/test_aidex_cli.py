from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.cli.parser import build_parser
from hermes_cgm_agent.services.aidex import AidexTokenResponse, AidexTokenStore
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AidexCLITests(unittest.TestCase):
    def test_parser_exposes_auth_and_incremental_sync(self) -> None:
        auth = build_parser().parse_args(["aidex-auth", "--user-id", "u"])
        sync = build_parser().parse_args(
            ["aidex-sync", "--user-id", "u", "--incremental", "--overlap-minutes", "20"]
        )
        status = build_parser().parse_args(["aidex-status", "--user-id", "u", "--live"])
        self.assertEqual(auth.command, "aidex-auth")
        self.assertTrue(sync.incremental)
        self.assertEqual(sync.overlap_minutes, 20)
        self.assertEqual(status.command, "aidex-status")
        self.assertTrue(status.live)

    def test_missing_credentials_fail_without_printing_secrets(self) -> None:
        from hermes_cgm_agent.cli.aidex import _aidex_sync

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            db_path = Path(tmp) / "app.db"
            self.assertEqual(
                _aidex_sync(
                    db_path=db_path,
                    user_id="u",
                    days=1,
                    force=False,
                    incremental=True,
                    overlap_minutes=15,
                    bootstrap_hours=24,
                ),
                1,
            )
            store = SQLiteStore(db_path)
            with store.connect() as conn:
                row = conn.execute(
                    "SELECT event_type, payload_json FROM audit_logs ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["event_type"], "aidex_sync_failed")
            payload = store.unseal(row["payload_json"], legacy="json")
            self.assertEqual(payload["user_id"], "u")

    def test_status_reports_readiness_without_exposing_credentials(self) -> None:
        from hermes_cgm_agent.cli.aidex import _aidex_status

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "app.db"
            scripts_dir = Path(tmp) / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "cgm_aidex_sync.py").write_text("# installed\n", encoding="utf-8")
            store = SQLiteStore(db_path)
            store.initialize()
            AidexTokenStore(store).save(
                "u",
                AidexTokenResponse(
                    access_token="do-not-print-access",
                    refresh_token="do-not-print-refresh",
                    expires_in=86400,
                    obtained_at=datetime.now(timezone.utc),
                ),
                environment="sandbox",
            )
            output = io.StringIO()
            with patch.dict(
                os.environ,
                {
                    "AIDEX_CLIENT_ID": "client",
                    "AIDEX_CLIENT_SECRET": "do-not-print-secret",
                    "AIDEX_USE_SANDBOX": "true",
                    "CGM_AGENT_USER_ID": "u",
                    "HERMES_HOME": tmp,
                },
                clear=True,
            ), contextlib.redirect_stdout(output):
                exit_code = _aidex_status(db_path=db_path, user_id="u", live=False)

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ready_for_sync"])
            self.assertTrue(payload["ready_for_automation"])
            self.assertNotIn("do-not-print", output.getvalue())
