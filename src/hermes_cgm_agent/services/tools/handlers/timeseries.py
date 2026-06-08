from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import DataScope, EvidenceRef
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.arguments import parse_limit
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse
from hermes_cgm_agent.services.tools.handlers.helpers import aggregate_ref, point_ref


class TimeseriesHandlerMixin(BaseToolHandler):
    def _get_points(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("timeseries.get_points")
        try:
            scope = DataScope.model_validate(arguments.get("data_scope"))
            limit = parse_limit(arguments.get("limit"))
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=str(exc),
            )

        points = self.repository.list_glucose_points(scope)
        if limit is not None:
            points = points[:limit]
        evidence_refs = [
            EvidenceRef(
                kind="glucose_point",
                ref_id=point_ref(point),
                summary=f"{point.timestamp.isoformat()} {point.value} {point.unit}",
            ).model_dump(mode="json")
            for point in points
        ]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": scope.model_dump(mode="json"),
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "point_count": len(points),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "points": [point.model_dump(mode="json") for point in points],
            },
        )

    def _get_aggregate(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("timeseries.get_aggregate")
        try:
            scope = DataScope.model_validate(arguments.get("data_scope"))
            window_label = arguments.get("window_label")
        except (TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=str(exc),
            )

        points = self.repository.list_glucose_points(scope)
        aggregate = CGMAnalyticsService().compute_aggregate(
            points=points,
            scope=scope,
            window_label=window_label,
        )
        evidence_refs = [
            EvidenceRef(
                kind="aggregate",
                ref_id=aggregate_ref(scope, window_label),
                summary=f"{aggregate.point_count} valid points, coverage={aggregate.data_coverage}%",
            ).model_dump(mode="json")
        ]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": scope.model_dump(mode="json"),
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "aggregate": aggregate.model_dump(mode="json", by_alias=True),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "aggregate": aggregate.model_dump(mode="json", by_alias=True),
            },
        )
