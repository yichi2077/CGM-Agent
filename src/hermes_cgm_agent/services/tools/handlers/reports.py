from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.services.memory import SQLiteMemoryRepository
from hermes_cgm_agent.services.reports import ReportToolService, SQLiteReportRepository
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse


class ReportHandlerMixin(BaseToolHandler):
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
