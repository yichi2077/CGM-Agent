from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    G8MemoryCandidate,
    HypothesisState,
    MemoryCandidate,
    MemoryLayer,
    ReportInput,
    UserEvent,
)
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom import (
    DexcomAuthError,
    DexcomError,
    DexcomSyncService,
    build_dexcom_sync_service,
)
from hermes_cgm_agent.services.memory import (
    L0ContextBuilder,
    MemoryContextAssembler,
    MemoryReviewService,
    SQLiteMemoryRepository,
    UserMDSyncService,
)
from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.reports import ReportService, SQLiteReportRepository
from hermes_cgm_agent.services.safety import (
    assert_track_isolation,
    query_number_coverage,
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
        dexcom_sync_factory: Callable[[SQLiteCGMRepository], DexcomSyncService] | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.registry = registry or build_default_tool_registry()
        self._rag_service: AuthoritativeRAGService | None = None
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
            limit = _parse_limit(arguments.get("limit"))
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
            confirmed = _require_bool(arguments.get("confirmed"), "confirmed")
            correction = arguments.get("correction")
            if correction is not None and not isinstance(correction, dict):
                raise ValueError("correction must be an object when provided")
            saved = self.repository.confirm_user_event(
                event_id,
                user_id=user_id,
                confirmed=confirmed,
                correction=correction,
            )
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

        evidence_refs = [_event_evidence(saved, action="confirmed" if confirmed else "rejected")]
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
                "confirmed": confirmed,
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
            # `retrieve_context` is a tool-level flag, not a ReportInput field.
            args = dict(arguments)
            retrieve_context = bool(args.pop("retrieve_context", False))
            auto_ingest_memory = _auto_ingest_memory_enabled(args)
            args.pop("auto_ingest_memory", None)
            if retrieve_context:
                args = self._inject_retrieved_context(args)
            report_input = ReportInput.model_validate(args)
            report_repository = SQLiteReportRepository(self.repository.store)
            report = ReportService(
                cgm_repository=self.repository,
                report_repository=report_repository,
            ).generate(report_input)
            memory_ingest = self._ingest_report_memory_candidates(
                report=report,
                enabled=auto_ingest_memory,
            )
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

    def _ingest_report_memory_candidates(self, *, report: Any, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {
                "enabled": False,
                "enqueued": 0,
                "auto_accepted": 0,
                "pending": 0,
            }
        candidates = [
            _report_candidate_to_memory_candidate(report, candidate, index)
            for index, candidate in enumerate(report.g8_memory_candidates, start=1)
        ]
        if not candidates:
            return {
                "enabled": True,
                "enqueued": 0,
                "auto_accepted": 0,
                "pending": 0,
            }
        review = MemoryReviewService(
            repository=SQLiteMemoryRepository(self.repository.store)
        )
        result = review.ingest_report_candidates(candidates)
        return {
            "enabled": True,
            "enqueued": result.enqueued,
            "auto_accepted": result.auto_accepted,
            "pending": result.pending,
        }

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
            confirmed = _require_bool(arguments.get("confirmed"), "confirmed")
            review = MemoryReviewService(
                repository=SQLiteMemoryRepository(self.repository.store)
            )
            resolved = review.confirm_candidate(
                candidate_id, user_id=user_id, confirmed=confirmed
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope={"user_id": arguments.get("user_id")},
                message=str(exc),
            )
        status_value = getattr(resolved.status, "value", resolved.status)
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
            layer = str(arguments["layer"])
            limit = _parse_limit(arguments.get("limit"))
            include_archived = bool(arguments.get("include_archived", False))
            repository = SQLiteMemoryRepository(self.repository.store)
            memories = self._list_memories(
                repository=repository,
                user_id=user_id,
                layer=layer,
                include_archived=include_archived,
            )
            if limit is not None:
                memories = memories[:limit]
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
                "total_count": len(memories),
                "include_archived": include_archived,
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"memories": memories, "total_count": len(memories)},
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
            layer = str(arguments["layer"])
            repository = SQLiteMemoryRepository(self.repository.store)
            deleted = self._delete_memory(
                repository=repository,
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
            target = str(arguments["target"])
            correction = arguments["correction"]
            if not isinstance(correction, dict):
                raise ValueError("correction must be an object")
            review = MemoryReviewService(
                repository=SQLiteMemoryRepository(self.repository.store)
            )
            memory_id = review.correct(user_id=user_id, target=target, correction=correction)
            hermes_home = os.environ.get("HERMES_HOME")
            if memory_id and target.upper() == "L2" and hermes_home:
                UserMDSyncService(
                    repository=SQLiteMemoryRepository(self.repository.store)
                ).sync(user_id=user_id, hermes_home=hermes_home)
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

    def _inject_retrieved_context(self, args: dict[str, Any]) -> dict[str, Any]:
        """Populate memory_context / authoritative_context from the memory + KB
        tracks before report generation. Facts are NOT touched (D013): this only
        adds source-tracked background the report renders into its RAG-aware slots.
        Caller-supplied contexts win (we do not overwrite explicit input)."""
        user_id = args.get("user_id") or (args.get("data_scope") or {}).get("user_id")
        if not user_id:
            return args
        assembler = MemoryContextAssembler(
            repository=SQLiteMemoryRepository(self.repository.store)
        )
        report_type = str(args.get("report_type", "daily"))
        query = f"{report_type} review for {user_id}"
        if "memory_context" not in args:
            args["memory_context"] = assembler.build_memory_context(
                user_id=str(user_id), query=query
            ).model_dump(mode="json")
        if "authoritative_context" not in args:
            args["authoritative_context"] = assembler.build_authoritative_context(
                query=query
            ).model_dump(mode="json")
        # D031: fail loudly if the two memory tracks ever cross-contaminate.
        assert_track_isolation(
            memory_items=(args.get("memory_context") or {}).get("items", []),
            authoritative_documents=(args.get("authoritative_context") or {}).get("documents", []),
        )
        return args

    def _rag_search(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("rag.authoritative_search")
        try:
            query = str(arguments["query"]).strip()
            if not query:
                raise ValueError("query must be a non-empty string")
            top_k = int(arguments.get("top_k", 3))
            if top_k < 1 or top_k > 20:
                raise ValueError("top_k must be between 1 and 20")
            population = arguments.get("population")
            if population is not None:
                population = str(population).strip() or None
            if self._rag_service is None:
                self._rag_service = AuthoritativeRAGService()
            documents = self._rag_service.search(
                query,
                top_k=top_k,
                population=population,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=None,
                message=str(exc),
            )
        # authoritative_kb evidence is kept on its own track, never mixed with user_memory
        evidence_refs = [doc["evidence_ref"] for doc in documents]
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": None,
                "risk_level": spec.risk_level,
                "evidence_refs": evidence_refs,
                "kb_version": self._rag_service.kb_version,
                "result_count": len(documents),
            },
        )
        # NOTE: this is a retrieval-coverage hint (which numbers in the user's
        # query are absent from the retrieved evidence), NOT anti-hallucination.
        # Hallucination guarding runs over GENERATED text in the skill/generation
        # layer via assert_authoritative_quotes (see skills/cgm-safety/SKILL.md).
        coverage = query_number_coverage(documents, query)
        payload = {
            "documents": documents,
            "kb_version": self._rag_service.kb_version,
            "quote_instruction": "verbatim_only",
        }
        if coverage.violations:
            payload["query_number_coverage"] = {
                "mode": coverage.mode,
                "uncovered": coverage.violations,
            }
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload=payload,
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
            state = HypothesisState(str(arguments["state"]))
            raw_refs = arguments.get("evidence_refs") or []
            if not isinstance(raw_refs, list):
                raise ValueError("evidence_refs must be a list when provided")
            evidence_refs = [EvidenceRef.model_validate(ref) for ref in raw_refs]
            repository = SQLiteMemoryRepository(self.repository.store)
            existing = {h.hypothesis_id: h for h in repository.list_hypotheses(user_id)}
            hypothesis = existing.get(hypothesis_id)
            if hypothesis is None:
                raise KeyError(f"Unknown hypothesis: {hypothesis_id}")
            hypothesis.state = state
            if evidence_refs:
                # Merge new evidence; keep the existing proof count monotonic.
                hypothesis.evidence_refs = [*hypothesis.evidence_refs, *evidence_refs]
                hypothesis.evidence_count = len(hypothesis.evidence_refs)
            from hermes_cgm_agent.domain.cgm import utc_now

            hypothesis.last_checked = utc_now()
            hypothesis.updated_at = utc_now()
            saved = repository.upsert_hypothesis(hypothesis)
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
            user_id = str(arguments["user_id"])
            days = int(arguments.get("days", 7))
            if days < 1 or days > 90:
                raise ValueError("days must be between 1 and 90")
            force = arguments.get("force", False)
            if not isinstance(force, bool):
                raise ValueError("force must be a boolean")
            sync_service = self._dexcom_sync_factory(self.repository)
            result = sync_service.sync(user_id=user_id, days=days, force=force)
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

        payload = result.to_dict()
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": {
                    "user_id": user_id,
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

    def _list_memories(
        self,
        *,
        repository: SQLiteMemoryRepository,
        user_id: str,
        layer: str,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        normalized = layer.lower()
        if normalized not in {"l1", "l2", "l3", "all"}:
            raise ValueError("layer must be one of: L1, L2, L3, all")
        memories: list[dict[str, Any]] = []
        if normalized in {"l1", "all"}:
            for episode in repository.list_episodes(user_id, include_archived=include_archived):
                item = episode.model_dump(mode="json")
                item["layer"] = "L1"
                item["memory_id"] = episode.episode_id
                memories.append(item)
        if normalized in {"l2", "all"}:
            for profile in repository.list_profile_items(user_id, active_only=not include_archived):
                item = profile.model_dump(mode="json")
                item["layer"] = "L2"
                item["memory_id"] = profile.item_id
                memories.append(item)
        if normalized in {"l3", "all"}:
            states = None if include_archived else [
                HypothesisState.CANDIDATE,
                HypothesisState.OBSERVING,
                HypothesisState.STABLE,
            ]
            for hypothesis in repository.list_hypotheses(user_id, states=states):
                item = hypothesis.model_dump(mode="json")
                item["layer"] = "L3"
                item["memory_id"] = hypothesis.hypothesis_id
                memories.append(item)
        return memories

    def _delete_memory(
        self,
        *,
        repository: SQLiteMemoryRepository,
        user_id: str,
        memory_id: str,
        layer: str,
    ) -> bool:
        normalized = layer.upper()
        if normalized == "L1":
            episode = repository.get_episode(memory_id)
            if episode is None or episode.user_id != user_id:
                return False
            return repository.delete_episode(memory_id)
        if normalized == "L2":
            items = {item.item_id: item for item in repository.list_profile_items(user_id, active_only=False)}
            if memory_id not in items:
                return False
            return repository.delete_profile_item(memory_id)
        if normalized == "L3":
            hypotheses = {item.hypothesis_id: item for item in repository.list_hypotheses(user_id, states=[
                HypothesisState.CANDIDATE,
                HypothesisState.OBSERVING,
                HypothesisState.STABLE,
                HypothesisState.ARCHIVED,
            ])}
            if memory_id not in hypotheses:
                return False
            return repository.delete_hypothesis(memory_id)
        raise ValueError("layer must be one of: L1, L2, L3")

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


def _require_bool(value: Any, field: str) -> bool:
    # C3: strict boolean. A JSON string like "false" is truthy in Python and
    # must NOT be coerced; reject non-bool so a rejection cannot become accept.
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _auto_ingest_memory_enabled(arguments: dict[str, Any]) -> bool:
    value = arguments.get("auto_ingest_memory")
    if value is not None:
        return _require_bool(value, "auto_ingest_memory")
    # Doctor-facing reports should not silently queue personal memory because
    # their audience and wording are optimized for clinicians, not self-review.
    return str(arguments.get("report_type", "")).lower() != "doctor"


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _report_candidate_to_memory_candidate(
    report: Any,
    candidate: G8MemoryCandidate,
    index: int,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=f"report-{report.report_id}-{index}",
        user_id=report.user_id,
        target_layer=MemoryLayer(candidate.target_layer),
        candidate_type=candidate.candidate_type,
        summary=candidate.summary,
        requires_user_confirmation=candidate.requires_user_confirmation,
        source_report_id=candidate.source_report_id or report.report_id,
        source_section_id=candidate.source_section_id,
        evidence_refs=candidate.evidence_refs,
        confidence=candidate.confidence,
    )


def _parse_limit(value: Any) -> int | None:
    if value is None:
        return None
    limit = int(value)
    if limit < 1 or limit > 10000:
        raise ValueError("limit must be between 1 and 10000")
    return limit


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
