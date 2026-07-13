from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from hermes_cgm_agent.services.tools.handlers.helpers import json_safe

if TYPE_CHECKING:
    from hermes_cgm_agent.services.audit import AuditService
    from hermes_cgm_agent.services.data import SQLiteCGMRepository
    from hermes_cgm_agent.services.tools.registry import ToolRegistry


class ToolStatus(str, Enum):
    """Semantic tool-execution outcomes.

    ``ok``/``error`` alone masked meaningfully different situations (empty
    query results, partial delivery, missing resources, throttling) behind
    ``status="ok"`` — the model then had to re-derive the real outcome from
    payload fields. Values are lowercase strings for wire back-compat.
    """

    OK = "ok"
    NO_DATA = "no_data"          # query succeeded but returned nothing
    PARTIAL = "partial"          # partially succeeded (e.g. delivery queued/failed locally)
    NOT_FOUND = "not_found"      # the requested resource does not exist
    RATE_LIMITED = "rate_limited"  # upstream throttled the request
    ERROR = "error"


# Statuses a CLI wrapper should surface as a non-zero exit code. no_data and
# partial are successful executions whose payload tells the caller what
# happened; the request itself did not fail.
FAILURE_STATUSES = frozenset({"error", "not_found", "rate_limited"})


@dataclass(frozen=True)
class ToolExecutionResponse:
    status: ToolStatus
    evidence_refs: list[dict[str, Any]]
    audit_id: str | None
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        """Validate and normalize legacy string callers at the response boundary."""
        object.__setattr__(self, "status", ToolStatus(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            # Normalize ToolStatus members to their wire value so audit/JSON
            # consumers always see the plain lowercase string.
            "status": self.status.value,
            "evidence_refs": self.evidence_refs,
            "audit_id": self.audit_id,
            **self.payload,
        }


def describe_argument_error(exc: Exception) -> str:
    """Actionable message for tool-argument failures (D054).

    ``str(KeyError('user_id'))`` is just ``"'user_id'"`` — an LLM (or human)
    reading the tool error cannot tell what went wrong or how to fix it.
    Every handler's argument-parsing ``except`` block routes through here.
    """
    if isinstance(exc, KeyError) and exc.args:
        return f"missing required argument: {exc.args[0]}"
    return str(exc)


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
