from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from hermes_cgm_agent.domain import CandidateStatus, DataScope, EvidenceRef, UserEvent
from hermes_cgm_agent.services.arguments import require_enum


def parse_candidate_status(value: Any) -> CandidateStatus | None:
    if value is None:
        return CandidateStatus.PENDING
    status = require_enum(value, "candidate_status", ("pending", "accepted", "rejected", "all"))
    if status == "all":
        return None
    return CandidateStatus(status)


def optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def point_ref(point: Any) -> str:
    return f"{point.user_id}:{point.timestamp.isoformat()}:{point.source}"


def aggregate_ref(scope: DataScope, window_label: Any) -> str:
    label = window_label or "window"
    source = scope.source or "all"
    return f"{scope.user_id}:{scope.window_start.isoformat()}:{scope.window_end.isoformat()}:{source}:{label}"


def event_evidence(event: UserEvent, *, action: str) -> dict[str, Any]:
    return EvidenceRef(
        kind="event",
        ref_id=event.event_id,
        summary=f"{action}: {event.event_type} at {event.ts_start.isoformat()}",
    ).model_dump(mode="json")


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))
