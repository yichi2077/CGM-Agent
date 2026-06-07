from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import (
    CandidateStatus,
    DataScope,
    EvidenceRef,
    UserEvent,
)
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.data import EventToolService, SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom import (
    DexcomAuthError,
    DexcomError,
    DexcomSyncFactory,
    DexcomSyncToolService,
    build_dexcom_sync_service,
)
from hermes_cgm_agent.services.memory import (
    L0ContextBuilder,
    MemoryToolService,
    SQLiteMemoryRepository,
)
from hermes_cgm_agent.services.rag import AuthoritativeRAGToolService
from hermes_cgm_agent.services.reports import ReportToolService, SQLiteReportRepository
from hermes_cgm_agent.services.arguments import (
    optional_bool,
    optional_int,
    parse_limit,
    require_bool,
    require_enum,
)
from hermes_cgm_agent.services.tools.registry import ToolRegistry, build_default_tool_registry


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


class ToolExecutor:
    def __init__(
        self,
        *,
        repository: SQLiteCGMRepository,
        audit_service: AuditService,
        registry: ToolRegistry | None = None,
        dexcom_sync_factory: DexcomSyncFactory | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.registry = registry or build_default_tool_registry()
        self._rag_tool_service: AuthoritativeRAGToolService | None = None
        # Seam for tests: a factory that builds a DexcomSyncService from the
        # repository. Defaults to env-sourced wiring (build_dexcom_sync_service).
        self._dexcom_sync_factory = dexcom_sync_factory or build_dexcom_sync_service

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        try:
            spec = self.registry.get(tool_name)
        except KeyError as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level="unknown",
                data_scope=None,
                message=str(exc),
            )

        if spec.status != "active":
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=f"Tool is not active: {tool_name}",
            )

        if tool_name == "timeseries.get_points":
            return self._get_points(arguments=arguments, session_id=session_id)
        if tool_name == "timeseries.get_aggregate":
            return self._get_aggregate(arguments=arguments, session_id=session_id)
        if tool_name == "events.create":
            return self._create_event(arguments=arguments, session_id=session_id)
        if tool_name == "events.confirm":
            return self._confirm_event(arguments=arguments, session_id=session_id)
        if tool_name == "context.get_l0":
            return self._get_l0_context(arguments=arguments, session_id=session_id)
        if tool_name == "reports.generate":
            return self._generate_report(arguments=arguments, session_id=session_id)
        if tool_name == "memory.list":
            return self._memory_list(arguments=arguments, session_id=session_id)
        if tool_name == "memory.delete":
            return self._memory_delete(arguments=arguments, session_id=session_id)
        if tool_name == "memory.confirm":
            return self._memory_confirm(arguments=arguments, session_id=session_id)
        if tool_name == "memory.correct":
            return self._memory_correct(arguments=arguments, session_id=session_id)
        if tool_name == "rag.authoritative_search":
            return self._rag_search(arguments=arguments, session_id=session_id)
        if tool_name == "rag.verify_quotes":
            return self._verify_quotes(arguments=arguments, session_id=session_id)
        if tool_name == "hypothesis.update":
            return self._hypothesis_update(arguments=arguments, session_id=session_id)
        if tool_name == "delivery.send":
            return self._delivery_send(arguments=arguments, session_id=session_id)
        if tool_name == "data.dexcom_sync":
            return self._dexcom_sync(arguments=arguments, session_id=session_id)

        return self._error_response(
            session_id=session_id,
            tool_name=tool_name,
            risk_level=spec.risk_level,
            data_scope=arguments.get("data_scope"),
            message=f"Tool has no executor: {tool_name}",
        )

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
                ref_id=_point_ref(point),
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
                ref_id=_aggregate_ref(scope, window_label),
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

    def _create_event(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("events.create")
        try:
            user_id = str(arguments["user_id"])
            event = UserEvent.model_validate(arguments.get("event"))
            if event.user_id != user_id:
                raise ValueError("event.user_id must match user_id")
            if event.created_by == "agent" and event.user_confirmed:
                raise ValueError("agent-created events must be unconfirmed candidates")
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )

        event_id = self.repository.create_user_event(event)
        saved = self.repository.get_user_event(event_id, include_rejected=True)
        evidence_refs = [_event_evidence(saved, action="created")]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": saved.user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "event_id": saved.event_id,
                "user_confirmed": saved.user_confirmed,
                "is_rejected": saved.is_rejected,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "event_id": saved.event_id,
                "event": saved.model_dump(mode="json", by_alias=True),
            },
        )

    def _get_l0_context(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("context.get_l0")
        try:
            user_id = str(arguments["user_id"])
            anchor_at = _optional_datetime(arguments.get("anchor_at"))
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

    def _confirm_event(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("events.confirm")
        try:
            user_id = str(arguments["user_id"])
            event_id = str(arguments["event_id"])
            result = EventToolService(self.repository).confirm_event(arguments)
            saved = result.event
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={
                    "user_id": arguments.get("user_id"),
                    "event_id": arguments.get("event_id"),
                },
                message=str(exc),
            )

        evidence_refs = [
            _event_evidence(saved, action="confirmed" if result.confirmed else "rejected")
        ]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": saved.user_id, "event_id": saved.event_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "event_id": saved.event_id,
                "confirmed": result.confirmed,
                "user_confirmed": saved.user_confirmed,
                "is_rejected": saved.is_rejected,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "event_id": saved.event_id,
                "event": saved.model_dump(mode="json", by_alias=True),
            },
        )

    def _generate_report(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("reports.generate")
        try:
            report_repository = SQLiteReportRepository(self.repository.store)
            result = ReportToolService(
                cgm_repository=self.repository,
                report_repository=report_repository,
                memory_repository=SQLiteMemoryRepository(self.repository.store),
            ).generate(
                arguments,
            )
            report = result.report
            memory_ingest = result.memory_ingest
        except (TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope") or {"user_id": arguments.get("user_id")},
                message=str(exc),
            )

        evidence_refs = [ref.model_dump(mode="json") for ref in report.evidence_refs]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": report.data_scope.model_dump(mode="json"),
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "report_id": report.report_id,
                "report_type": report.report_type,
                "template_version": report.template_version,
                "output_hash": report.output_hash,
                "route": report.route,
                "safety_result": report.safety_result,
                "section_count": len(report.sections),
                "memory_ingest": memory_ingest,
                "data_quality_warnings": [
                    warning.model_dump(mode="json")
                    for warning in report.data_quality_warnings
                ],
            },
        )
        report = report_repository.update_audit_id(report.report_id, audit_id)
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={
                "report_id": report.report_id,
                "report": report.model_dump(mode="json"),
                "sections": [section.model_dump(mode="json") for section in report.sections],
                "rendered_markdown": report.rendered_markdown,
                "g8_memory_candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in report.g8_memory_candidates
                ],
                "memory_ingest": memory_ingest,
            },
        )

    def _memory_confirm(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("memory.confirm")
        try:
            user_id = str(arguments["user_id"])
            candidate_id = str(arguments["candidate_id"])
            confirmed = require_bool(arguments.get("confirmed"), "confirmed")
            status_value = MemoryToolService(
                SQLiteMemoryRepository(self.repository.store)
            ).confirm_candidate(
                user_id=user_id,
                candidate_id=candidate_id,
                confirmed=confirmed,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
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
                "candidate_id": candidate_id,
                "candidate_status": status_value,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"candidate_id": candidate_id, "candidate_status": status_value},
        )

    def _memory_list(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("memory.list")
        try:
            user_id = str(arguments["user_id"])
            layer = require_enum(
                arguments["layer"],
                "layer",
                ("L1", "L2", "L3", "all", "candidates"),
            )
            limit = parse_limit(arguments.get("limit"))
            include_archived = optional_bool(
                arguments.get("include_archived"),
                "include_archived",
                default=False,
            )
            candidate_status = _parse_candidate_status(arguments.get("candidate_status"))
            repository = SQLiteMemoryRepository(self.repository.store)
            result = MemoryToolService(repository).list_records(
                user_id=user_id,
                layer=layer,
                include_archived=include_archived,
                candidate_status=candidate_status,
                limit=limit,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id, "layer": layer},
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                "total_count": result.total_count,
                "candidate_count": result.candidate_count,
                "include_archived": include_archived,
                "candidate_status": (
                    candidate_status.value if candidate_status is not None else "all"
                ),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={
                "memories": result.memories,
                "total_count": result.total_count,
                "candidates": result.candidates,
                "candidate_count": result.candidate_count,
            },
        )

    def _memory_delete(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("memory.delete")
        try:
            user_id = str(arguments["user_id"])
            memory_id = str(arguments["memory_id"])
            layer = require_enum(arguments["layer"], "layer", ("L1", "L2", "L3"))
            repository = SQLiteMemoryRepository(self.repository.store)
            deleted = MemoryToolService(repository).delete_record(
                user_id=user_id,
                memory_id=memory_id,
                layer=layer,
            )
            if not deleted:
                raise KeyError(f"Unknown memory record: {layer}:{memory_id}")
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id, "layer": layer},
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                "deleted_id": memory_id,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"deleted_id": memory_id, "layer": layer},
        )

    def _memory_correct(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("memory.correct")
        try:
            user_id = str(arguments["user_id"])
            target = require_enum(arguments["target"], "target", ("L1", "L2", "L3"))
            correction = arguments["correction"]
            if not isinstance(correction, dict):
                raise ValueError("correction must be an object")
            memory_id = MemoryToolService(
                SQLiteMemoryRepository(self.repository.store)
            ).correct_memory(
                user_id=user_id,
                target=target,
                correction=correction,
                hermes_home=os.environ.get("HERMES_HOME"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
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
                "target": target,
                "memory_id": memory_id,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"memory_id": memory_id},
        )

    def _rag_search(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("rag.authoritative_search")
        try:
            if self._rag_tool_service is None:
                self._rag_tool_service = AuthoritativeRAGToolService()
            result = self._rag_tool_service.search(arguments)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=None,
                message=str(exc),
            )
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": None,
                "risk_level": spec.risk_level,
                "evidence_refs": result.evidence_refs,
                "kb_version": result.kb_version,
                "result_count": len(result.documents),
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=result.evidence_refs,
            audit_id=audit_id,
            payload=result.payload,
        )

    def _verify_quotes(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("rag.verify_quotes")
        try:
            if self._rag_tool_service is None:
                self._rag_tool_service = AuthoritativeRAGToolService()
            result = self._rag_tool_service.verify_quotes(arguments)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=None,
                message=str(exc),
            )
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": None,
                "risk_level": spec.risk_level,
                "guard_ok": result.ok,
                "guard_mode": result.mode,
                "violation_count": len(result.violations),
                "checked_documents": result.checked_documents,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={
                "ok": result.ok,
                "mode": result.mode,
                "violations": result.violations,
                "checked_documents": result.checked_documents,
            },
        )

    def _hypothesis_update(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("hypothesis.update")
        try:
            user_id = str(arguments["user_id"])
            hypothesis_id = str(arguments["hypothesis_id"])
            saved = MemoryToolService(
                SQLiteMemoryRepository(self.repository.store)
            ).update_hypothesis(
                user_id=user_id,
                hypothesis_id=hypothesis_id,
                state=arguments["state"],
                evidence_refs=arguments.get("evidence_refs"),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )
        evidence_payload = [ref.model_dump(mode="json") for ref in saved.evidence_refs]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {"user_id": user_id},
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_payload,
                "hypothesis_id": saved.hypothesis_id,
                "state": saved.state.value,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_payload,
            audit_id=audit_id,
            payload={"hypothesis_id": saved.hypothesis_id, "state": saved.state.value},
        )

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
        # local_file is fully handled here; remote channels (email/webhook) are
        # not configured in the capability layer and are recorded as queued so a
        # gateway/cron deliver step (Hermes-owned) can fulfil them. We never
        # silently claim a remote send succeeded.
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

    def _dexcom_sync(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("data.dexcom_sync")
        try:
            result = DexcomSyncToolService(
                repository=self.repository,
                sync_factory=self._dexcom_sync_factory,
            ).sync(arguments)
        except DexcomAuthError as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=f"Dexcom authorization required: {exc}",
            )
        except (DexcomError, KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )

        payload = result.payload
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {
                    "user_id": result.user_id,
                    "window_start": payload["window_start"],
                    "window_end": payload["window_end"],
                },
                "risk_level": spec.risk_level,
                "evidence_refs": [],
                **payload,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload=payload,
        )

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
                "data_scope": _json_safe(data_scope),
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


def _parse_candidate_status(value: Any) -> CandidateStatus | None:
    if value is None:
        return CandidateStatus.PENDING
    status = require_enum(value, "candidate_status", ("pending", "accepted", "rejected", "all"))
    if status == "all":
        return None
    return CandidateStatus(status)


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _point_ref(point: Any) -> str:
    return f"{point.user_id}:{point.timestamp.isoformat()}:{point.source}"


def _aggregate_ref(scope: DataScope, window_label: Any) -> str:
    label = window_label or "window"
    source = scope.source or "all"
    return f"{scope.user_id}:{scope.window_start.isoformat()}:{scope.window_end.isoformat()}:{source}:{label}"


def _event_evidence(event: UserEvent, *, action: str) -> dict[str, Any]:
    return EvidenceRef(
        kind="event",
        ref_id=event.event_id,
        summary=f"{action}: {event.event_type} at {event.ts_start.isoformat()}",
    ).model_dump(mode="json")


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
