from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from hermes_cgm_agent.services.tools.handlers.base import BaseToolHandler, ToolExecutionResponse


class DeliveryHandlerMixin(BaseToolHandler):
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
