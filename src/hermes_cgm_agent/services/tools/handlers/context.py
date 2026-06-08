from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import EvidenceRef
from hermes_cgm_agent.services.memory import L0ContextBuilder
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse
from hermes_cgm_agent.services.tools.handlers.helpers import optional_datetime


class ContextHandlerMixin(BaseToolHandler):
    def _get_l0_context(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("context.get_l0")
        try:
            user_id = str(arguments["user_id"])
            anchor_at = optional_datetime(arguments.get("anchor_at"))
            source = arguments.get("source")
            if source is not None:
                source = str(source)
            context = L0ContextBuilder(repository=self.repository).build(
                user_id=user_id,
                anchor_at=anchor_at,
                source=source,
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )
        evidence_refs = [
            EvidenceRef(
                kind="aggregate",
                ref_id=(
                    f"{context.window.user_id}:"
                    f"{context.window.window_start.isoformat()}:"
                    f"{context.window.window_end.isoformat()}:L0"
                ),
                summary=(
                    f"L0 context with {len(context.high_res_recent)} recent points, "
                    f"{len(context.mid_far_hourly)} hourly summaries"
                ),
            ).model_dump(mode="json")
        ]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": context.window.model_dump(mode="json"),
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "estimated_tokens": context.estimated_tokens,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={"context": context.model_dump(mode="json")},
        )
