"""F5/US2 webhook delivery tests (T014-T017b).

The webhook HTTP POST is exercised through ToolExecutor.execute() with the
network boundary (``urllib.request.OpenerDirector.open``) patched, so no real
request leaves the box. The no-redirect security guarantee (analyze S1) is
proven deterministically — the production ``_NoRedirectHandler`` refuses to
follow, the opener wires it in place of urllib's default redirect handler, and
a 302 is treated as a failed single POST — without spinning up a real server.
The PHI allowlist filter is unit-tested directly.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore

WEBHOOK_URL = "https://hooks.example.com/cgm"
WEBHOOK_DOMAIN = "hooks.example.com"


class _FakeResponse:
    """Stand-in for an http.client.HTTPResponse used as a context manager."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _WebhookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.repository = SQLiteCGMRepository(self.store)
        self.session_id = "webhook-delivery-test"
        self.executor = ToolExecutor(
            repository=self.repository,
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _send(self, *, webhook_url: str | None = None, payload_ref: str = "push:abc123") -> dict:
        env = {k: v for k, v in os.environ.items() if k != "CGM_WEBHOOK_URL"}
        if webhook_url is not None:
            env["CGM_WEBHOOK_URL"] = webhook_url
        with patch.dict(os.environ, env, clear=True):
            return self.executor.execute(
                tool_name="delivery.send",
                session_id=self.session_id,
                arguments={"user_id": "u1", "channel": "webhook", "payload_ref": payload_ref},
            ).to_dict()

    def _last_audit(self) -> dict:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM audit_logs WHERE session_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return self.store.unseal(row["payload_json"], legacy="json")


class WebhookDeliveryTests(_WebhookTestBase):
    def test_successful_post_returns_sent(self) -> None:
        # SC-003: a 2xx response yields delivery_status=sent with a POST to the
        # configured URL carrying a JSON body and the JSON content type.
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.return_value = _FakeResponse(200)
            body = self._send(webhook_url=WEBHOOK_URL)

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["delivery_status"], "sent")
        mock_open.assert_called_once()
        request = mock_open.call_args.args[0]
        self.assertEqual(request.full_url, WEBHOOK_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("application/json", request.headers.values())
        sent_body = json.loads(request.data)
        self.assertIsInstance(sent_body, dict)
        # payload_ref becomes push_id (analyze U4); no user identity in the body.
        self.assertEqual(sent_body["push_id"], "push:abc123")
        self.assertNotIn("user_id", sent_body)


class WebhookFailureTests(_WebhookTestBase):
    def test_missing_env_returns_failed_without_request(self) -> None:
        # SC-005 / FR-009: no CGM_WEBHOOK_URL -> failed, zero HTTP calls.
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            body = self._send(webhook_url=None)
        self.assertEqual(body["delivery_status"], "failed")
        mock_open.assert_not_called()

    def test_http_500_returns_failed(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                WEBHOOK_URL, 500, "Server Error", {}, None
            )
            body = self._send(webhook_url=WEBHOOK_URL)
        self.assertEqual(body["delivery_status"], "failed")

    def test_timeout_returns_failed(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.side_effect = TimeoutError("timed out")
            body = self._send(webhook_url=WEBHOOK_URL)
        self.assertEqual(body["delivery_status"], "failed")

    def test_invalid_url_returns_failed_without_request(self) -> None:
        # C5: a malformed URL (no host) is a configuration error -> failed, no call.
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            body = self._send(webhook_url="https://")
        self.assertEqual(body["delivery_status"], "failed")
        mock_open.assert_not_called()


class PHIFilterTests(unittest.TestCase):
    """T015 / C3: the hard-coded allowlist is the security boundary; it is
    deny-by-default and applies to any manifest."""

    def _filter(self, manifest: dict) -> dict:
        from hermes_cgm_agent.services.tools.handlers.delivery import _filter_webhook_payload

        return _filter_webhook_payload(manifest)

    def test_only_allowed_keys_pass(self) -> None:
        out = self._filter(
            {
                "delivery_id": "d1",
                "push_id": "p1",
                "tier": "daily",
                "period_key": "2026-06-09",
                "metrics": {"tir_pct": 72.0, "mean_mgdl": 140.0, "gmi": 6.5},
                "event_summaries": [{"type": "meal", "count": 3}],
                "delivered_at": "2026-06-09T01:00:00+00:00",
            }
        )
        self.assertEqual(
            set(out),
            {
                "delivery_id",
                "push_id",
                "tier",
                "period_key",
                "metrics",
                "event_summaries",
                "delivered_at",
            },
        )
        self.assertEqual(set(out["metrics"]), {"tir_pct", "mean_mgdl", "gmi"})

    def test_injected_phi_is_stripped(self) -> None:
        out = self._filter(
            {
                "delivery_id": "d1",
                "user_id": "alice",
                "content": "你今天血糖很好",
                "points": [{"value": 95, "ts": "..."}],
                "session_id": "s-secret",
                "metrics": {"tir_pct": 72.0, "raw_series": [95, 96]},
            }
        )
        for denied in ("user_id", "content", "points", "session_id"):
            self.assertNotIn(denied, out)
        self.assertNotIn("raw_series", out["metrics"])

    def test_event_summaries_reduced_to_type_and_count(self) -> None:
        out = self._filter(
            {
                "delivery_id": "d1",
                "event_summaries": [
                    {"type": "exercise", "count": 2, "raw_points": [1, 2, 3], "user_id": "x"}
                ],
            }
        )
        self.assertEqual(out["event_summaries"], [{"type": "exercise", "count": 2}])


class WebhookAuditTests(_WebhookTestBase):
    def test_success_audit_has_domain_and_status_no_full_url(self) -> None:
        # C4: audit records domain only + http_status_code; never the full URL,
        # request body, response body, or PHI.
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.return_value = _FakeResponse(200)
            self._send(webhook_url=WEBHOOK_URL)
        audit = self._last_audit()

        self.assertEqual(audit["channel"], "webhook")
        self.assertEqual(audit["delivery_status"], "sent")
        self.assertEqual(audit["delivery_url_domain"], WEBHOOK_DOMAIN)
        self.assertEqual(audit["http_status_code"], 200)
        audit_blob = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("https://", audit_blob)  # no scheme/full URL
        self.assertNotIn("/cgm", audit_blob)  # no URL path
        self.assertNotIn("push:abc123", audit_blob)  # no payload_ref / body

    def test_failure_audit_has_error_type(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                WEBHOOK_URL, 500, "Server Error", {}, None
            )
            self._send(webhook_url=WEBHOOK_URL)
        audit = self._last_audit()
        self.assertEqual(audit["delivery_status"], "failed")
        self.assertIsNotNone(audit["error_type"])


class WebhookSecurityTests(_WebhookTestBase):
    """T017b / analyze S1: https-only + never follow redirects. Verified
    deterministically (no real server) at three layers: the handler refuses,
    the opener is wired with it, and a 302 is a failed single POST."""

    def test_http_scheme_rejected_without_request(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            body = self._send(webhook_url="http://hooks.example.com/cgm")
        self.assertEqual(body["delivery_status"], "failed")
        mock_open.assert_not_called()

    def test_no_redirect_handler_returns_none(self) -> None:
        # The mechanism: urllib calls redirect_request to build the follow-up
        # request; returning None makes it raise HTTPError instead of following.
        from hermes_cgm_agent.services.tools.handlers.delivery import _NoRedirectHandler

        result = _NoRedirectHandler().redirect_request(
            urllib.request.Request("https://a.example.com/"),
            None,
            302,
            "Found",
            {},
            "https://other.example.com/",
        )
        self.assertIsNone(result)

    def test_opener_only_carries_the_no_redirect_handler(self) -> None:
        # Wiring: the opener's redirect handler is OUR no-redirect subclass, so
        # urllib's default (following) handler is not in play.
        from hermes_cgm_agent.services.tools.handlers.delivery import (
            _NoRedirectHandler,
            _build_no_redirect_opener,
        )

        opener = _build_no_redirect_opener()
        redirect_handlers = [
            h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)
        ]
        self.assertTrue(redirect_handlers)
        self.assertTrue(all(isinstance(h, _NoRedirectHandler) for h in redirect_handlers))

    def test_302_is_failed_and_not_followed(self) -> None:
        # Behavior: a 302 (what the no-redirect opener raises rather than
        # following) is a failed delivery from a SINGLE POST — the Location
        # target is never contacted.
        with patch("urllib.request.OpenerDirector.open") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                WEBHOOK_URL, 302, "Found", {"Location": "https://relay.example.com/trap"}, None
            )
            body = self._send(webhook_url=WEBHOOK_URL)
        self.assertEqual(body["delivery_status"], "failed")
        mock_open.assert_called_once()  # single POST; the 302 was not followed
        self.assertEqual(self._last_audit()["http_status_code"], 302)


if __name__ == "__main__":
    unittest.main()
