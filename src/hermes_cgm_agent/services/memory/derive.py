from __future__ import annotations

from datetime import datetime

from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain import GlucoseEvent, L1Episode


def episodes_from_detected_events(
    events: list[GlucoseEvent], *, now: datetime, timezone_name: str | None = None
) -> list[L1Episode]:
    """Derive deterministic L1 episodes from detected glucose events.

    DATA_GAP events are excluded (D052): a sensor/collector outage is a data
    quality fact, not a user behavior pattern — letting it consolidate into an
    L2 "recurring data_gap" belief and L3 hypothesis pollutes the memory the
    companion narrative reasons over. Gaps stay visible via detected events
    and data-quality warnings.
    """
    # D058: L1 episodes are recalled into every conversation, so their summary
    # must be the companion's Chinese life-language (in the user's display
    # unit), not the detector's raw English clinical string. The English form
    # stays on the GlucoseEvent for clinician/audit paths.
    # NOTE: render_episode_summary import stays inline to avoid circular dep
    # (services.memory → services.reports.narrative_templates → services.reports
    # → services.reports.tools → services.memory).
    from hermes_cgm_agent.services.reports.narrative_templates import render_episode_summary

    # L-06: use the caller-provided timezone (e.g. report timezone) instead of
    # always falling back to default_timezone().
    tz = timezone_name or default_timezone()
    episodes: list[L1Episode] = []
    for event in events:
        if getattr(event.event_type, "value", event.event_type) == "data_gap":
            continue
        episodes.append(
            L1Episode(
                episode_id=f"evt-{event.event_id}",
                user_id=event.user_id,
                occurred_at=event.ts_start,
                episode_type=getattr(event.event_type, "value", event.event_type),
                summary=render_episode_summary(event, timezone_name=tz),
                evidence_refs=event.evidence_refs,
                confidence=0.9,
                created_at=now,
                last_referenced_at=now,
            )
        )
    return episodes
