from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.domain import UserEvent
from hermes_cgm_agent.services.arguments import require_bool
from hermes_cgm_agent.services.data.repository import SQLiteCGMRepository


@dataclass(frozen=True)
class EventToolResult:
    event: UserEvent
    confirmed: bool


class EventToolService:
    """Tool-facing orchestration for event write actions."""

    def __init__(self, repository: SQLiteCGMRepository) -> None:
        self.repository = repository

    def confirm_event(self, arguments: dict[str, Any]) -> EventToolResult:
        user_id = str(arguments["user_id"])
        event_id = str(arguments["event_id"])
        confirmed = require_bool(arguments.get("confirmed"), "confirmed")
        correction = arguments.get("correction")
        if correction is not None and not isinstance(correction, dict):
            raise ValueError("correction must be an object when provided")
        saved = self.repository.confirm_user_event(
            event_id,
            user_id=user_id,
            confirmed=confirmed,
            correction=correction,
        )
        return EventToolResult(event=saved, confirmed=confirmed)
