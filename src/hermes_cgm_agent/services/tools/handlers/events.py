from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.domain import UserEvent
from hermes_cgm_agent.services.data import EventToolService
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse
from hermes_cgm_agent.services.tools.handlers.helpers import event_evidence


class EventHandlerMixin(BaseToolHandler):
    def _create_event(
        self,
        *,
        arguments: dict[str, Any],
        session_id: str,
    ) -> ToolExecutionResponse:
        spec = self.registry.get("events.create")
        try:
            user_id = str(arguments["user_id"])
            event_raw = arguments.get("event")
            if not isinstance(event_raw, dict):
                raise ValueError("event must be an object")
            event_raw = dict(event_raw)
            # Force technical/provenance fields server-side (D045 / FR-007, Damocles W2):
            # the model supplies only event_type + ts_start (+ optional ts_end/payload/
            # confidence). The id, owner, provenance and confirmation flag are NOT
            # model-controllable, so an agent-created event can never masquerade as a
            # user-authored or user-confirmed fact.
            event_raw["event_id"] = uuid.uuid4().hex
            event_raw["user_id"] = user_id
            event_raw["created_by"] = "agent"
            event_raw["user_confirmed"] = False
            event = UserEvent.model_validate(event_raw)
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
        evidence_refs = [event_evidence(saved, action="created")]
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
            event_evidence(saved, action="confirmed" if result.confirmed else "rejected")
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
