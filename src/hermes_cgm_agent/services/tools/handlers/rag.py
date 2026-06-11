from __future__ import annotations

import uuid
from typing import Any

from hermes_cgm_agent.services.rag import AuthoritativeRAGToolService
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse


class RagHandlerMixin(BaseToolHandler):
    # Lazily built and cached on first rag.* call; declared here for readers.
    _rag_tool_service: AuthoritativeRAGToolService | None

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

    def _kb_approve(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("kb.approve")
        try:
            if self._rag_tool_service is None:
                self._rag_tool_service = AuthoritativeRAGToolService()
            result = self._rag_tool_service.approve(arguments)
        except (KeyError, TypeError, ValueError) as exc:
            return self._error_response(
                session_id=session_id,
                tool_name=spec.name,
                risk_level=spec.risk_level,
                data_scope=None,
                message=str(exc),
            )
        approval_id = uuid.uuid4().hex
        # Audit records the sign-off provenance only — never claim text (FR-013 /
        # SEC-003): card_id + reviewer + verified flag, no card body.
        audit_id = self.audit_service.log(
            session_id=session_id,
            event_type="tool_call",
            payload={
                "tool_name": spec.name,
                "status": "ok",
                "data_scope": None,
                "risk_level": spec.risk_level,
                "approval_id": approval_id,
                "card_id": result["card_id"],
                "reviewer": result["reviewer"],
                "verified": result["verified"],
            },
        )
        return ToolExecutionResponse(
            status="ok",
            evidence_refs=[],
            audit_id=audit_id,
            payload={"approval_id": approval_id, **result},
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
