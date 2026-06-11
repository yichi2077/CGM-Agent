from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse

# Webhook HTTP POST is a single at-most-once call: 10s timeout, no retry (retry
# is a Hermes/cron concern, not this layer's — FR-008).
_WEBHOOK_TIMEOUT_SECONDS = 10

# PHI allowlist (Constitution Principle VII / plan.md "PHI Protection"). This is
# THE security boundary: deny-by-default, applied to any manifest before it
# leaves the box. Only non-identifying aggregate metadata may pass.
_WEBHOOK_ALLOWED_TOP = ("delivery_id", "push_id", "tier", "period_key", "delivered_at")
_WEBHOOK_ALLOWED_METRICS = ("tir_pct", "mean_mgdl", "gmi")
_WEBHOOK_ALLOWED_EVENT_KEYS = ("type", "count")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow 3xx redirects (analyze S1): a redirected POST must never
    divert an aggregate-health payload to another host. Returning None from
    ``redirect_request`` makes urllib raise ``HTTPError`` for the 3xx instead of
    re-sending the body to the ``Location`` target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _build_no_redirect_opener() -> urllib.request.OpenerDirector:
    """An opener whose only redirect handler refuses to follow (analyze S1):
    ``build_opener`` replaces urllib's default redirect-following handler with
    our ``_NoRedirectHandler`` because it is a subclass of it."""
    return urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    """POST through the no-redirect opener (single at-most-once call)."""
    return _build_no_redirect_opener().open(request, timeout=timeout)


def _filter_webhook_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    """Apply the hard-coded PHI allowlist to a delivery manifest (C3 / FR-006/7).

    Deny-by-default: only allowlisted top-level keys survive, ``metrics`` is
    reduced to the three aggregate measures, and each ``event_summaries`` entry
    is reduced to ``type`` + ``count``. Everything else (user_id, content, raw
    glucose points, session_id, credentials, free-text narrative) is stripped."""
    filtered: dict[str, Any] = {}
    for key in _WEBHOOK_ALLOWED_TOP:
        if key in manifest:
            filtered[key] = manifest[key]
    metrics = manifest.get("metrics")
    if isinstance(metrics, dict):
        allowed_metrics = {k: metrics[k] for k in _WEBHOOK_ALLOWED_METRICS if k in metrics}
        if allowed_metrics:
            filtered["metrics"] = allowed_metrics
    summaries = manifest.get("event_summaries")
    if isinstance(summaries, list):
        filtered["event_summaries"] = [
            {k: item[k] for k in _WEBHOOK_ALLOWED_EVENT_KEYS if k in item}
            for item in summaries
            if isinstance(item, dict)
        ]
    return filtered


class DeliveryHandlerMixin(BaseToolHandler):
    def _delivery_send(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("delivery.send")
        try:
            user_id = str(arguments["user_id"])
            channel = str(arguments["channel"])
            payload_ref = str(arguments["payload_ref"])
            if channel not in {"local_file", "email", "webhook"}:
                raise ValueError(f"Unsupported delivery channel: {channel}")
            if not payload_ref.strip():
                raise ValueError("payload_ref must be a non-empty reference")
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )

        delivery_id = uuid.uuid4().hex

        # webhook is the first remote channel implemented in the capability
        # layer; it owns its own PHI-redacted audit path and returns directly.
        if channel == "webhook":
            return self._deliver_webhook(
                spec=spec,
                session_id=session_id,
                user_id=user_id,
                payload_ref=payload_ref,
                arguments=arguments,
                delivery_id=delivery_id,
            )

        # local_file is fully handled here; email is still recorded as queued so
        # a Hermes-owned gateway can fulfil it. We never silently claim a remote
        # send succeeded.
        delivery_status = "failed"
        manifest_path: str | None = None
        if channel == "local_file":
            target_dir = Path(self.repository.store.db_path).resolve().parent / "deliveries"
            target_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "delivery_id": delivery_id,
                "user_id": user_id,
                "channel": channel,
                "payload_ref": payload_ref,
                "session_id": session_id,
            }
            out = target_dir / f"{delivery_id}.json"
            out.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            manifest_path = str(out)
            delivery_status = "sent"
        else:
            delivery_status = "queued"

        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                "delivery_id": delivery_id,
                "channel": channel,
                "payload_ref": payload_ref,
                "delivery_status": delivery_status,
                "manifest_path": manifest_path,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={
                "delivery_id": delivery_id,
                "delivery_status": delivery_status,
                "manifest_path": manifest_path,
            },
        )

    def _deliver_webhook(
        self,
        *,
        spec: Any,
        session_id: str,
        user_id: str,
        payload_ref: str,
        arguments: dict[str, Any],
        delivery_id: str,
    ) -> ToolExecutionResponse:
        # Endpoint comes from the environment ONLY (FR-011): the model cannot
        # supply or redirect the URL through tool arguments.
        url = os.environ.get("CGM_WEBHOOK_URL")
        delivery_status = "failed"
        delivery_url_domain: str | None = None
        http_status_code: int | None = None
        error_type: str | None = None

        if not url:
            error_type = "not_configured"
        else:
            parsed = urllib.parse.urlparse(url)
            delivery_url_domain = parsed.hostname  # domain only, never the full URL
            if parsed.scheme != "https":
                # https-only: aggregate health metrics must not go cleartext (S1).
                error_type = "invalid_url"
            elif not parsed.hostname:
                error_type = "invalid_url"
            else:
                manifest: dict[str, Any] = {
                    "delivery_id": delivery_id,
                    "push_id": payload_ref,  # payload_ref IS the push id (analyze U4)
                    "delivered_at": utc_now().isoformat(),
                }
                # v1 is metadata-first; aggregate metrics/event summaries ride
                # along only when a caller already resolved them. The allowlist
                # filter is the boundary regardless of what the manifest holds.
                if arguments.get("tier") is not None:
                    manifest["tier"] = arguments["tier"]
                if arguments.get("period_key") is not None:
                    manifest["period_key"] = arguments["period_key"]
                if isinstance(arguments.get("metrics"), dict):
                    manifest["metrics"] = arguments["metrics"]
                if isinstance(arguments.get("event_summaries"), list):
                    manifest["event_summaries"] = arguments["event_summaries"]

                body = json.dumps(
                    _filter_webhook_payload(manifest), ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
                request = urllib.request.Request(
                    url,
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with _urlopen_no_redirect(request, timeout=_WEBHOOK_TIMEOUT_SECONDS) as resp:
                        http_status_code = getattr(resp, "status", None)
                        if http_status_code is not None and 200 <= http_status_code < 300:
                            delivery_status = "sent"
                        else:
                            error_type = "http_error"
                except urllib.error.HTTPError as exc:
                    http_status_code = exc.code
                    error_type = "http_error"
                    if exc.fp is not None:
                        exc.close()  # release the un-followed 3xx / error socket
                except urllib.error.URLError as exc:
                    error_type = "timeout" if isinstance(exc.reason, TimeoutError) else "connection_error"
                except TimeoutError:
                    error_type = "timeout"
                except Exception:  # noqa: BLE001 — at-most-once: any failure is audited, not raised
                    error_type = "connection_error"

        # Audit (C4 / FR-010): domain only (never full URL), status code on
        # success, error type on failure. No PHI, no payload body, no credentials.
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                "delivery_id": delivery_id,
                "channel": "webhook",
                "delivery_status": delivery_status,
                "delivery_url_domain": delivery_url_domain,
                "http_status_code": http_status_code,
                "error_type": error_type,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={
                "delivery_id": delivery_id,
                "delivery_status": delivery_status,
                "manifest_path": None,
            },
        )
