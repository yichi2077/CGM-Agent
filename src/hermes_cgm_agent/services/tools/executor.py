from __future__ import annotations

from typing import Any

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.dexcom import (
    DexcomSyncFactory,
    build_dexcom_sync_service,
)
from hermes_cgm_agent.services.rag import AuthoritativeRAGToolService
from hermes_cgm_agent.services.tools.handlers import (
    ContextHandlerMixin,
    DeliveryHandlerMixin,
    DexcomHandlerMixin,
    EventHandlerMixin,
    MemoryHandlerMixin,
    RagHandlerMixin,
    ReportHandlerMixin,
    TimeseriesHandlerMixin,
    ToolExecutionResponse,
)
from hermes_cgm_agent.services.tools.registry import ToolRegistry, build_default_tool_registry

# Re-exported for back-compat: callers import ToolExecutionResponse from this module.
__all__ = ["ToolExecutionResponse", "ToolExecutor"]


class ToolExecutor(
    TimeseriesHandlerMixin,
    EventHandlerMixin,
    ContextHandlerMixin,
    ReportHandlerMixin,
    MemoryHandlerMixin,
    RagHandlerMixin,
    DeliveryHandlerMixin,
    DexcomHandlerMixin,
):
    """Routes a tool call to its per-domain handler (defined in the handler
    mixins) and owns the shared wiring: repository, audit service, registry,
    and the lazily-built rag/dexcom seams."""

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

    _DISPATCH = {
        "timeseries.get_points": "_get_points",
        "timeseries.get_aggregate": "_get_aggregate",
        "events.create": "_create_event",
        "events.confirm": "_confirm_event",
        "context.get_l0": "_get_l0_context",
        "reports.generate": "_generate_report",
        "memory.list": "_memory_list",
        "memory.delete": "_memory_delete",
        "memory.confirm": "_memory_confirm",
        "memory.correct": "_memory_correct",
        "rag.authoritative_search": "_rag_search",
        "rag.verify_quotes": "_verify_quotes",
        "kb.approve": "_kb_approve",
        "hypothesis.update": "_hypothesis_update",
        "delivery.send": "_delivery_send",
        "data.dexcom_sync": "_dexcom_sync",
    }

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

        handler_name = self._DISPATCH.get(tool_name)
        if handler_name is None:
            return self._error_response(
                session_id=session_id,
                tool_name=tool_name,
                risk_level=spec.risk_level,
                data_scope=arguments.get("data_scope"),
                message=f"Tool has no executor: {tool_name}",
            )

        handler = getattr(self, handler_name)
        return handler(arguments=arguments, session_id=session_id)
