"""Delivery channel behavior: email freeze (D050) and the push->delivery
back-write bridge (D052).

email is a FROZEN KNOWN GAP: delivery.send must record it as ``queued`` with no
side effects and never claim a remote send. local_file / webhook deliveries that
reach ``sent`` back-write ``push_events.delivery_id`` when their ``payload_ref``
names a real push row (closing the F5 last-mile audit gap).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class _DeliveryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session_id = "delivery-channel-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _send(self, **arguments: object) -> dict:
        arguments.setdefault("user_id", "u1")
        return self.executor.execute(
            tool_name="delivery.send",
            session_id=self.session_id,
            arguments=arguments,
        ).to_dict()

    def _insert_push(
        self, *, push_id: str, user_id: str = "u1", tier: str = "daily",
        period_key: str = "2026-07-01", delivery_id: object = None,
    ) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO push_events
                    (push_id, user_id, tier, period_key, summary_id, delivery_id, pushed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (push_id, user_id, tier, period_key, "sum-1", delivery_id, "2026-07-01T09:00:00+00:00"),
            )

    def _delivery_id_of(self, push_id: str) -> object:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT delivery_id FROM push_events WHERE push_id = ?", (push_id,)
            ).fetchone()
        self.assertIsNotNone(row)
        return row["delivery_id"]


class EmailFreezeTests(_DeliveryTestBase):
    def test_email_channel_is_queued_with_no_side_effects(self) -> None:
        # D050: email is frozen — recorded queued, never sent. Pins the contract
        # so a future change can't silently claim a remote email succeeded.
        body = self._send(channel="email", payload_ref="push:abc")
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["delivery_status"], "queued")
        self.assertIsNone(body["manifest_path"])
        # No deliveries directory is created for a queued email.
        deliveries = Path(self.store.db_path).resolve().parent / "deliveries"
        self.assertFalse(deliveries.exists())


class LocalFileBridgeTests(_DeliveryTestBase):
    def test_local_file_backwrites_delivery_id(self) -> None:
        self._insert_push(push_id="push-1")
        body = self._send(channel="local_file", payload_ref="push-1")
        self.assertEqual(body["delivery_status"], "sent")
        self.assertEqual(self._delivery_id_of("push-1"), body["delivery_id"])

    def test_unknown_payload_ref_is_noop(self) -> None:
        # A delivery whose payload_ref names no push row still succeeds; nothing
        # to back-write, no error.
        body = self._send(channel="local_file", payload_ref="not-a-push")
        self.assertEqual(body["delivery_status"], "sent")

    def test_second_delivery_does_not_overwrite_delivery_id(self) -> None:
        # IS NULL guard: once linked, a later delivery for the same push_id must
        # not clobber the recorded delivery_id.
        self._insert_push(push_id="push-2", delivery_id="already-linked")
        self._send(channel="local_file", payload_ref="push-2")
        self.assertEqual(self._delivery_id_of("push-2"), "already-linked")


class WebhookBridgeTests(_DeliveryTestBase):
    def test_webhook_sent_backwrites_delivery_id(self) -> None:
        from tests.test_webhook_delivery import _FakeResponse  # reuse fake 2xx

        self._insert_push(push_id="push-3")
        env = {k: v for k, v in os.environ.items() if k != "CGM_WEBHOOK_URL"}
        env["CGM_WEBHOOK_URL"] = "https://hooks.example.com/cgm"
        with patch.dict(os.environ, env, clear=True), patch(
            "urllib.request.OpenerDirector.open"
        ) as mock_open:
            mock_open.return_value = _FakeResponse(200)
            body = self._send(channel="webhook", payload_ref="push-3")
        self.assertEqual(body["delivery_status"], "sent")
        self.assertEqual(self._delivery_id_of("push-3"), body["delivery_id"])


if __name__ == "__main__":
    unittest.main()
