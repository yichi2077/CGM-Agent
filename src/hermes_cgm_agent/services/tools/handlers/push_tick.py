from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from hermes_cgm_agent.domain import ensure_utc
from hermes_cgm_agent.services.scheduling import PushSchedulerService
from hermes_cgm_agent.services.tools.handlers.base import (
    BaseToolHandler,
    ToolExecutionResponse,
    describe_argument_error,
)


class PushTickHandlerMixin(BaseToolHandler):
    """F5 D1: wrap ``PushSchedulerService.push_tick()`` as a Hermes-invocable tool.

    Hermes cron *triggers* the tick (``user_id`` + optional ``now``); the
    scheduling policy, tier selection, content generation and silent-consent
    logic all stay inside ``PushSchedulerService``. The capability layer owns
    policy/content/state, Hermes owns the cadence (Principle VII). The model
    cannot influence anything beyond which user to tick and (for testing) the
    clock.
    """

    def _push_tick(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("scheduling.push_tick")
        try:
            user_id = arguments["user_id"]
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValueError("user_id must be a non-empty string")
            now_arg = arguments.get("now")
            now_dt: datetime | None = None
            if now_arg is not None:
                if not isinstance(now_arg, str):
                    raise ValueError("now must be an ISO-8601 datetime string")
                # Hermes cron and LLM callers routinely omit the offset;
                # naive == UTC (ensure_utc).
                now_dt = ensure_utc(datetime.fromisoformat(now_arg.replace("Z", "+00:00")))
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=describe_argument_error(exc),
            )

        # The scheduler owns policy/content/state; pass the audit service through
        # so silent-consent advances are recorded at the domain level.
        service = PushSchedulerService(
            store=self.repository.store,
            audit_service=self.audit_service,
        )
        result = service.push_tick(user_id=user_id, now=now_dt)

        # Last-mile delivery (D053): when a webhook endpoint is configured, a
        # successful push is delivered in the same tick instead of relying on
        # the operator to wire a second cron step. Reuses the delivery mixin
        # on this executor — PHI allowlist, https-only, no-redirect, and the
        # domain-only audit all apply unchanged. Absent endpoint -> no-op.
        deliveries: list[dict[str, Any]] = []
        if os.environ.get("CGM_WEBHOOK_URL"):
            for entry in result.pushed:
                delivery_response = self._delivery_send(
                    arguments={
                        "user_id": user_id,
                        "channel": "webhook",
                        "payload_ref": str(entry.get("push_id") or ""),
                        "tier": entry.get("tier"),
                        "period_key": entry.get("period_key"),
                    },
                    session_id=session_id,
                )
                deliveries.append(
                    {
                        "push_id": entry.get("push_id"),
                        "tier": entry.get("tier"),
                        "delivery_status": delivery_response.payload.get("delivery_status"),
                        "delivery_id": delivery_response.payload.get("delivery_id"),
                    }
                )

        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                "pushed_tiers": [entry["tier"] for entry in result.pushed],
                "silent_consent_count": len(result.silent_consent),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={
                "user_id": result.user_id,
                "now": result.now,
                "pushed": list(result.pushed),
                "silent_consent": list(result.silent_consent),
                "deliveries": deliveries,
            },
        )
