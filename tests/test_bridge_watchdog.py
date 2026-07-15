"""Data-freshness watchdog tests (D064).

Covers the edge-triggered transition matrix, the PHI-free payload boundary, the
webhook sender's https-only/no-retry contract, and the end-to-end orchestrator
(prior-state read from audit logs, best-effort failure handling).
"""

from __future__ import annotations

import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.services.sources.watchdog import (
    build_watchdog_payload,
    decide_watchdog_alert,
    read_prior_bridge_state,
    run_bridge_watchdog,
    send_watchdog_alert,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class DecideWatchdogAlertTests(unittest.TestCase):
    """The transition matrix: alerts are edge-triggered on the healthy boundary."""

    def test_healthy_to_stale_alerts_degraded(self) -> None:
        self.assertEqual(
            decide_watchdog_alert(prior_state="healthy", current_state="stale"),
            "degraded",
        )

    def test_stale_to_healthy_alerts_recovered(self) -> None:
        self.assertEqual(
            decide_watchdog_alert(prior_state="stale", current_state="healthy"),
            "recovered",
        )

    def test_stale_to_stale_is_silent(self) -> None:
        # The core anti-noise property: a bridge that stays stale is not
        # re-alerted every minute.
        self.assertIsNone(
            decide_watchdog_alert(prior_state="stale", current_state="stale")
        )

    def test_stale_to_failed_stays_silent_same_side_of_boundary(self) -> None:
        # Both are unhealthy; no boundary crossing, so no re-alert.
        self.assertIsNone(
            decide_watchdog_alert(prior_state="stale", current_state="failed")
        )

    def test_healthy_to_healthy_is_silent(self) -> None:
        self.assertIsNone(
            decide_watchdog_alert(prior_state="healthy", current_state="healthy")
        )

    def test_first_observation_healthy_is_silent(self) -> None:
        self.assertIsNone(
            decide_watchdog_alert(prior_state=None, current_state="healthy")
        )

    def test_first_observation_bad_alerts(self) -> None:
        # A bridge born stale/failed is worth one alert.
        self.assertEqual(
            decide_watchdog_alert(prior_state=None, current_state="failed"),
            "degraded",
        )

    def test_failed_to_healthy_recovers(self) -> None:
        self.assertEqual(
            decide_watchdog_alert(prior_state="failed", current_state="healthy"),
            "recovered",
        )


class WatchdogPayloadTests(unittest.TestCase):
    """The payload is the PHI boundary: only non-identifying keys, ever."""

    _ALLOWED_KEYS = {"alert", "state", "at", "newest_reading_age_minutes"}

    def test_payload_has_only_allowlisted_keys(self) -> None:
        payload = build_watchdog_payload(
            alert="degraded",
            state="stale",
            newest_reading_age_seconds=1500,
            now=datetime(2026, 7, 14, 3, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(set(payload).issubset(self._ALLOWED_KEYS))

    def test_payload_carries_no_glucose_or_identity(self) -> None:
        payload = build_watchdog_payload(
            alert="degraded",
            state="stale",
            newest_reading_age_seconds=1500,
        )
        for forbidden in ("sgv", "glucose", "value", "user_id", "mgdl", "mmol"):
            self.assertNotIn(forbidden, payload)

    def test_age_seconds_floored_to_minutes(self) -> None:
        payload = build_watchdog_payload(
            alert="degraded", state="stale", newest_reading_age_seconds=1500
        )
        self.assertEqual(payload["newest_reading_age_minutes"], 25)

    def test_alert_name_is_namespaced(self) -> None:
        payload = build_watchdog_payload(
            alert="recovered", state="healthy", newest_reading_age_seconds=60
        )
        self.assertEqual(payload["alert"], "cgm_bridge_recovered")

    def test_missing_age_omits_key(self) -> None:
        payload = build_watchdog_payload(
            alert="degraded", state="failed", newest_reading_age_seconds=None
        )
        self.assertNotIn("newest_reading_age_minutes", payload)


class SendWatchdogAlertTests(unittest.TestCase):
    def test_http_url_rejected_before_any_request(self) -> None:
        status, error, code = send_watchdog_alert(
            url="http://insecure.example/hook", payload={"alert": "x"}
        )
        self.assertEqual((status, error, code), ("failed", "invalid_url", None))

    def test_successful_post_reports_sent(self) -> None:
        class _Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        with patch("urllib.request.OpenerDirector.open", return_value=_Resp()):
            status, error, code = send_watchdog_alert(
                url="https://hooks.example/cgm", payload={"alert": "x"}
            )
        self.assertEqual((status, error), ("sent", None))
        self.assertEqual(code, 204)

    def test_http_error_is_caught_not_raised(self) -> None:
        def _raise(*_a, **_k):
            raise urllib.error.HTTPError(
                "https://hooks.example/cgm", 500, "err", {}, None
            )

        with patch("urllib.request.OpenerDirector.open", side_effect=_raise):
            status, error, code = send_watchdog_alert(
                url="https://hooks.example/cgm", payload={"alert": "x"}
            )
        self.assertEqual((status, error, code), ("failed", "http_error", 500))


class RunBridgeWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self._tmp.name) / "app.db")
        self.store.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_poll_event(self, event_type: str) -> None:
        self.store.create_audit_log(
            session_id="bridge-cli:u", event_type=event_type, payload={"x": 1}
        )

    def test_no_webhook_configured_is_noop(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CGM_WEBHOOK_URL", None)
            result = run_bridge_watchdog(
                store=self.store,
                current_state="stale",
                newest_reading_age_seconds=1500,
                session_id="bridge-cli:u",
            )
        self.assertIsNone(result)

    def test_read_prior_state_ignores_non_poll_events(self) -> None:
        self._seed_poll_event("bridge_poll_completed")
        self.store.create_audit_log(
            session_id="bridge-cli:u",
            event_type="bridge_watchdog_alert",
            payload={"alert": "cgm_bridge_degraded"},
        )
        # Most recent *poll* event is still the completed one.
        self.assertEqual(read_prior_bridge_state(self.store), "healthy")

    def test_boundary_crossing_sends_and_audits(self) -> None:
        self._seed_poll_event("bridge_poll_completed")  # prior healthy
        with patch.dict(os.environ, {"CGM_WEBHOOK_URL": "https://hooks.example/cgm"}):
            with patch(
                "hermes_cgm_agent.services.sources.watchdog.send_watchdog_alert",
                return_value=("sent", None, 204),
            ) as sender:
                audit = run_bridge_watchdog(
                    store=self.store,
                    current_state="stale",
                    newest_reading_age_seconds=1800,
                    session_id="bridge-cli:u",
                )
        sender.assert_called_once()
        # PHI boundary holds on the wire payload too.
        sent_payload = sender.call_args.kwargs["payload"]
        self.assertNotIn("user_id", sent_payload)
        self.assertEqual(sent_payload["alert"], "cgm_bridge_degraded")
        self.assertIsNotNone(audit)
        self.assertEqual(audit["delivery_status"], "sent")
        self.assertEqual(audit["delivery_url_domain"], "hooks.example")

    def test_no_boundary_no_send(self) -> None:
        self._seed_poll_event("bridge_poll_completed")  # prior healthy
        with patch.dict(os.environ, {"CGM_WEBHOOK_URL": "https://hooks.example/cgm"}):
            with patch(
                "hermes_cgm_agent.services.sources.watchdog.send_watchdog_alert"
            ) as sender:
                audit = run_bridge_watchdog(
                    store=self.store,
                    current_state="healthy",  # healthy -> healthy
                    newest_reading_age_seconds=60,
                    session_id="bridge-cli:u",
                )
        sender.assert_not_called()
        self.assertIsNone(audit)

    def test_watchdog_failure_is_swallowed_and_audited(self) -> None:
        self._seed_poll_event("bridge_poll_completed")
        with patch.dict(os.environ, {"CGM_WEBHOOK_URL": "https://hooks.example/cgm"}):
            with patch(
                "hermes_cgm_agent.services.sources.watchdog.send_watchdog_alert",
                side_effect=RuntimeError("boom"),
            ):
                # Must not raise — collection continuity beats alerting.
                result = run_bridge_watchdog(
                    store=self.store,
                    current_state="stale",
                    newest_reading_age_seconds=1800,
                    session_id="bridge-cli:u",
                )
        self.assertIsNone(result)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_logs WHERE event_type = 'bridge_watchdog_error'"
            ).fetchone()
        self.assertEqual(row["n"], 1)


if __name__ == "__main__":
    unittest.main()
