from __future__ import annotations

from hermes_cgm_agent.storage.sqlite import SessionRecord, SQLiteStore


class SessionService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def create(
        self,
        *,
        title: str | None = None,
        hermes_resume_id: str | None = None,
        hermes_continue_name: str | None = None,
    ) -> SessionRecord:
        return self.store.create_session(
            title=title,
            hermes_resume_id=hermes_resume_id,
            hermes_continue_name=hermes_continue_name,
        )

    def get(self, session_id: str) -> SessionRecord:
        return self.store.get_session(session_id)

    def list(self, *, limit: int = 50) -> list[SessionRecord]:
        return self.store.list_sessions(limit=limit)

    def delete(self, session_id: str) -> bool:
        return self.store.delete_session(session_id)
