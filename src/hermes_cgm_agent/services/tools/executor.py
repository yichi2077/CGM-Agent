from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    HypothesisState,
    ReportInput,
    UserEvent,
)
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import (
    MemoryContextAssembler,
    MemoryReviewService,
    SQLiteMemoryRepository,
)
from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.reports import ReportService, SQLiteReportRepository
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
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.registry = registry or build_default_tool_registry()
        self._rag_service: AuthoritativeRAGService | None = None

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
        if tool_name == "reports.generate":
            return self._generate_report(arguments=arguments, session_id=session_id)
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
            if retrieve_context:
                args = self._inject_retrieved_context(args)
            report_input = ReportInput.model_validate(args)
            report_repository = SQLiteReportRepository(self.repository.store)
            report = ReportService(
                cgm_repository=self.repository,
                report_repository=report_repository,
            ).generate(report_input)
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
            if self._rag_service is None:
                self._rag_service = AuthoritativeRAGService()
            documents = self._rag_service.search(query, top_k=top_k)
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
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=evidence_refs,
            audit_id=audit_id,
            payload={"documents": documents, "kb_version": self._rag_service.kb_version},
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
