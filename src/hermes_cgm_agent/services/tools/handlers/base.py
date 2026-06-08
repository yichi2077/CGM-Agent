from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hermes_cgm_agent.services.tools.handlers.helpers import json_safe

if TYPE_CHECKING:
    from hermes_cgm_agent.services.audit import AuditService
    from hermes_cgm_agent.services.data import SQLiteCGMRepository
    from hermes_cgm_agent.services.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolExecutionResponse:
    status: str
    evidence_refs: list[dict[str, Any]]
    audit_id: str | None
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evidence_refs": self.evidence_refs,
            "audit_id": self.audit_id,
            **self.payload,
        }


class BaseToolHandler:
    """Shared state + error path for the per-domain tool handler mixins.

    The attributes below are populated by ``ToolExecutor.__init__``; every
    handler mixin reads them through ``self``. They are declared here
    (annotation-only, no runtime assignment) so each domain module documents
    the executor contract it depends on, and so type checkers resolve the
    cross-mixin ``self`` access through the common base.
    """

    repository: "SQLiteCGMRepository"
    audit_service: "AuditService"
    registry: "ToolRegistry"

    def _error_response(
        self,
        *,
        session_id: str,
        tool_name: str,
        risk_level: str,
        data_scope: Any,
        message: str,
    ) -> ToolExecutionResponse:
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": tool_name,
                "status": "error",
                "data_scope": json_safe(data_scope),
                "risk_level": risk_level,
                "evidence_refs": [],
                "error": message,
            },
        )
        return ToolExecutionResponse(
            status="error",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"error": message},
        )
