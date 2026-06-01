from __future__ import annotations

from typing import Any

from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AuditService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def log(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str:
        return self.store.create_audit_log(
            session_id=session_id,
            event_type=event_type,
            payload=payload,
        )
