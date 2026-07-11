from __future__ import annotations

from dataclasses import asdict
from numbers import Real
from typing import Any

from hermes_cgm_agent.services.analytics.meal_correlation import MealCorrelationAnalyzer
from hermes_cgm_agent.services.tools.handlers.base import (
    BaseToolHandler,
    ToolExecutionResponse,
    describe_argument_error,
)
from hermes_cgm_agent.services.tools.handlers.helpers import event_evidence


class MealCorrelationHandlerMixin(BaseToolHandler):
    """Expose confirmed personal meal history through a scoped read-only tool."""

    def _find_similar_meals(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("meals.find_similar")
        try:
            user_id = str(arguments["user_id"]).strip()
            food_name = arguments["food_name"]
            if not user_id:
                raise ValueError("user_id must not be empty")
            if not isinstance(food_name, str) or not food_name.strip():
                raise ValueError("food_name must be a non-empty string")
            limit = _parse_limit(arguments.get("limit"))
            window_hours = _parse_window_hours(arguments.get("window_hours"))
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=describe_argument_error(exc),
            )

        matches = MealCorrelationAnalyzer().find_similar_meals(
            food_name,
            user_id,
            self.repository,
            limit=limit,
            window_hours=window_hours,
        )
        evidence_refs = [event_evidence(match.event, action="historical meal") for match in matches]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "match_count": len(matches),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "matches": [
                    {
                        "event": match.event.model_dump(mode="json", by_alias=True),
                        "matched_food_name": match.matched_food_name,
                        "response": asdict(match.response) if match.response is not None else None,
                    }
                    for match in matches
                ]
            },
        )


def _parse_limit(value: Any) -> int:
    if value is None:
        return 10
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("limit must be an integer from 1 to 50")
    return value


def _parse_window_hours(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real) or not 0 <= value <= 24:
        raise ValueError("window_hours must be a number from 0 to 24")
    return float(value)
