"""Data-freshness watchdog for the Android CGM bridge (D064).

The bridge cron (`cgm_bridge_poll.py`) already computes newest-reading age every
minute. This module turns that into an *edge-triggered* alert: it fires a
webhook only when bridge health crosses the healthy<->unhealthy boundary, so a
persistently-stale bridge is announced once, not every minute. The whole point
is to make silent overnight stalls loud without becoming noise.

Two invariants:
- **PHI-free payload.** The alert carries only ``alert``/``state``/
  ``newest_reading_age_minutes``/``at`` — never a glucose value, never a
  user id (Constitution VII). ``build_watchdog_payload`` is the boundary.
- **Best-effort.** Alerting must never break data collection. The orchestrator
  swallows every exception and audits it instead of raising.

Prior state is read from the most recent ``bridge_poll_*`` audit row, so there
is no new table and no separate watchdog cursor.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from hermes_cgm_agent.domain.cgm import utc_now

# Single at-most-once POST, matching the F5 webhook contract: 10s, no retry
# (retry is a cron concern, not this layer's).
_TIMEOUT_SECONDS = 10

# Maps the poll audit event types to a coarse health state. Anything not
# "healthy" is an unhealthy state for boundary detection.
_POLL_EVENT_STATE = {
    "bridge_poll_completed": "healthy",
    "bridge_poll_degraded": "stale",
    "bridge_poll_failed": "failed",
}

_HEALTHY = "healthy"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse 3xx redirects so a POST can never be diverted to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def decide_watchdog_alert(*, prior_state: str | None, current_state: str) -> str | None:
    """Return ``"degraded"``, ``"recovered"``, or ``None`` for a state transition.

    Edge-triggered: ``healthy`` is the good state, anything else is bad. An
    alert fires only when crossing the boundary, so a bridge that stays stale is
    not re-alerted each minute. The very first observation alerts only if it is
    already bad (a bridge born stale is worth knowing about).
    """
    current_good = current_state == _HEALTHY
    if prior_state is None:
        return None if current_good else "degraded"
    prior_good = prior_state == _HEALTHY
    if prior_good and not current_good:
        return "degraded"
    if not prior_good and current_good:
        return "recovered"
    return None


def read_prior_bridge_state(store: Any) -> str | None:
    """Return the health state of the most recent prior bridge poll, or None.

    Reads the newest ``bridge_poll_*`` audit row. Must be called BEFORE the
    current poll writes its own audit, so "most recent" is genuinely the prior
    poll. Non-poll events (e.g. ``bridge_watchdog_alert``) are filtered out.
    """
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT event_type FROM audit_logs
            WHERE event_type IN (
                'bridge_poll_completed', 'bridge_poll_degraded', 'bridge_poll_failed'
            )
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return _POLL_EVENT_STATE.get(row["event_type"])


def build_watchdog_payload(
    *,
    alert: str,
    state: str,
    newest_reading_age_seconds: float | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the PHI-free alert body. This is the security boundary: only these
    non-identifying keys are ever emitted — no glucose value, no user id."""
    payload: dict[str, Any] = {
        "alert": f"cgm_bridge_{alert}",
        "state": state,
        "at": (now or utc_now()).isoformat(),
    }
    if newest_reading_age_seconds is not None:
        payload["newest_reading_age_minutes"] = int(newest_reading_age_seconds // 60)
    return payload


def send_watchdog_alert(*, url: str, payload: dict[str, Any]) -> tuple[str, str | None, int | None]:
    """POST the alert. Returns ``(delivery_status, error_type, http_status)``.

    https-only, no-redirect, single 10s call, never raises. Mirrors the F5
    webhook security properties so aggregate/health signals never go cleartext
    or get redirected to another host.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return "failed", "invalid_url", None
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as resp:
            code = getattr(resp, "status", None)
            if code is not None and 200 <= code < 300:
                return "sent", None, code
            return "failed", "http_error", code
    except urllib.error.HTTPError as exc:
        if exc.fp is not None:
            exc.close()  # release the un-followed 3xx / error socket
        return "failed", "http_error", exc.code
    except urllib.error.URLError as exc:
        return "failed", "timeout" if isinstance(exc.reason, TimeoutError) else "connection_error", None
    except TimeoutError:
        return "failed", "timeout", None
    except Exception:  # noqa: BLE001 — best-effort: any failure is reported, not raised
        return "failed", "connection_error", None


def run_bridge_watchdog(
    *,
    store: Any,
    current_state: str,
    newest_reading_age_seconds: float | None,
    session_id: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Edge-triggered staleness alerting for the bridge cron.

    Reads the prior bridge state, and if health crossed the healthy<->unhealthy
    boundary, sends a PHI-free webhook to ``CGM_WEBHOOK_URL`` and audits the
    attempt (``bridge_watchdog_alert``). No-op when the webhook is unset.
    Best-effort: any failure is audited (``bridge_watchdog_error``), never raised.

    Returns the audit payload when an alert was attempted, else None.
    """
    url = (os.getenv("CGM_WEBHOOK_URL") or "").strip()
    if not url:
        return None
    try:
        prior_state = read_prior_bridge_state(store)
        alert = decide_watchdog_alert(prior_state=prior_state, current_state=current_state)
        if alert is None:
            return None
        payload = build_watchdog_payload(
            alert=alert,
            state=current_state,
            newest_reading_age_seconds=newest_reading_age_seconds,
            now=now,
        )
        delivery_status, error_type, http_status = send_watchdog_alert(url=url, payload=payload)
        audit = {
            "alert": payload["alert"],
            "state": current_state,
            "delivery_status": delivery_status,
            "delivery_url_domain": urllib.parse.urlparse(url).hostname,
            "http_status_code": http_status,
            "error_type": error_type,
        }
        store.create_audit_log(
            session_id=session_id,
            event_type="bridge_watchdog_alert",
            payload=audit,
        )
        return audit
    except Exception as exc:  # noqa: BLE001 — alerting must never break collection
        try:
            store.create_audit_log(
                session_id=session_id,
                event_type="bridge_watchdog_error",
                payload={"error": str(exc)},
            )
        except Exception:  # noqa: BLE001
            pass
        return None
