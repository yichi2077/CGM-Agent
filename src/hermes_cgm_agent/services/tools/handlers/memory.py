from __future__ import annotations

import os
from typing import Any

from pydantic import ValidationError

from hermes_cgm_agent.services.arguments import (
    optional_bool,
    parse_limit,
    require_bool,
    require_enum,
)
from hermes_cgm_agent.services.memory import MemoryToolService, SQLiteMemoryRepository
from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse
from hermes_cgm_agent.services.tools.handlers.helpers import parse_candidate_status


class MemoryHandlerMixin(BaseToolHandler):
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
            candidate_status = parse_candidate_status(arguments.get("candidate_status"))
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
