from __future__ import annotations

from typing import Any

from hermes_cgm_agent.services.dexcom import (
    DexcomAuthError,
    DexcomError,
    DexcomSyncFactory,
    DexcomSyncToolService,
)
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse


class DexcomHandlerMixin(BaseToolHandler):
    # Test seam: a factory that builds a DexcomSyncService from the repository.
    # Populated by ToolExecutor.__init__; declared here for readers.
    _dexcom_sync_factory: DexcomSyncFactory

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
