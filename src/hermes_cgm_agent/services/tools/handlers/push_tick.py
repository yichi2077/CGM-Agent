from __future__ import annotations

from datetime import datetime
from typing import Any

from hermes_cgm_agent.services.scheduling import PushSchedulerService
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse


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
                now_dt = datetime.fromisoformat(now_arg)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )

        # The scheduler owns policy/content/state; pass the audit service through
        # so silent-consent advances are recorded at the domain level.
        service = PushSchedulerService(
            store=self.repository.store,
            audit_service=self.audit_service,
        )
        result = service.push_tick(user_id=user_id, now=now_dt)

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
            },
        )
