from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import DataScope, EvidenceRef, ensure_utc
from hermes_cgm_agent.services.analytics import (
    AnalyticsConfig,
    CGMAnalyticsService,
    RealtimeSignalConfig,
    RealtimeSignalService,
)
from hermes_cgm_agent.services.arguments import parse_limit, require_enum
from hermes_cgm_agent.services.tools.handlers.base import (
    BaseToolHandler,
    ToolExecutionResponse,
    describe_argument_error,
)
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
                message=describe_argument_error(exc),
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
            # M-14: validate window_label against the schema enum when provided.
            # window_label is optional (defaults to None downstream).
            _raw_window = arguments.get("window_label")
            if _raw_window is not None:
                require_enum(
                    _raw_window,
                    "window_label",
                    ("day", "week", "14d", "month"),
                )
            window_label = _raw_window
            expected_interval = _parse_expected_interval(arguments.get("expected_interval_minutes"))
        except (TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=describe_argument_error(exc),
            )

        points = self.repository.list_glucose_points(scope)
        aggregate = CGMAnalyticsService(
            AnalyticsConfig(expected_interval_minutes=expected_interval)
        ).compute_aggregate(
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

    def _get_realtime_snapshot(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("timeseries.get_realtime_snapshot")
        try:
            scope = DataScope.model_validate(arguments.get("data_scope"))
            expected_interval = _parse_expected_interval(arguments.get("expected_interval_minutes"))
            stale_after = _parse_positive_int(arguments.get("stale_after_minutes"), default=10, max_value=240)
            now = _parse_optional_datetime(arguments.get("now"))
        except (TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=describe_argument_error(exc),
            )

        points = self.repository.list_glucose_points(scope)
        snapshot = RealtimeSignalService(
            RealtimeSignalConfig(
                expected_interval_minutes=expected_interval,
                stale_after_minutes=stale_after,
            )
        ).compute(points=points, scope=scope, now=now)
        evidence_refs = [
            EvidenceRef(
                kind="aggregate",
                ref_id=aggregate_ref(scope, "realtime"),
                summary=f"latest={snapshot.latest_glucose_mg_dl}, missing_rate_1h={snapshot.missing_rate_1h}%",
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
                "snapshot": snapshot.to_dict(),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={"snapshot": snapshot.to_dict()},
        )


def _parse_expected_interval(value: Any) -> int:
    return _parse_positive_int(value, default=5, max_value=60)


def _parse_positive_int(value: Any, *, default: int, max_value: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected interval settings must be integers")
    if value < 1:
        raise ValueError("expected interval settings must be positive")
    if value > max_value:
        raise ValueError(f"value must not exceed {max_value}")
    return value


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("now must be an ISO 8601 datetime string")
    # LLM callers omit offsets; naive == UTC (ensure_utc).
    return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
