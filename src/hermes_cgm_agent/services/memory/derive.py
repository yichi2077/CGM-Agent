from __future__ import annotations

from datetime import datetime

from hermes_cgm_agent.domain import GlucoseEvent, L1Episode


def episodes_from_detected_events(
    events: list[GlucoseEvent], *, now: datetime
) -> list[L1Episode]:
    """Derive deterministic L1 episodes from detected glucose events."""
    episodes: list[L1Episode] = []
    for event in events:
        episodes.append(
            L1Episode(
                episode_id=f"evt-{event.event_id}",
                user_id=event.user_id,
                occurred_at=event.ts_start,
                episode_type=getattr(event.event_type, "value", event.event_type),
                summary=event.summary,
                evidence_refs=event.evidence_refs,
                confidence=0.9,
                created_at=now,
                last_referenced_at=now,
            )
        )
    return episodes
