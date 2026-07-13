from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.sources.config import BridgeConfig
from hermes_cgm_agent.services.sources.http import HTTPSourceClient
from hermes_cgm_agent.services.sources.models import SourceKind
from hermes_cgm_agent.services.sources.parser import parse_source_payload


@dataclass(frozen=True)
class BridgeHealthResult:
    status: str
    kind: SourceKind
    url: str
    source: str
    fetched_count: int
    parsed_count: int
    issue_count: int
    newest_reading_at: str | None
    newest_reading_age_seconds: float | None
    stale: bool
    future_clock_skew: bool
    authenticated: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def check_bridge_health(
    config: BridgeConfig,
    *,
    client: HTTPSourceClient | None = None,
    now: datetime | None = None,
) -> BridgeHealthResult:
    resolved_client = client or config.build_client()
    resolved_url, payload = resolved_client.fetch_json(
        url=config.url,
        kind=config.kind,
        count=config.count,
    )
    parsed = parse_source_payload(payload, kind=config.kind)
    moment = _as_utc(now or utc_now())
    newest = max((reading.measured_at for reading in parsed.readings), default=None)
    age_seconds = (moment - newest).total_seconds() if newest else None
    stale = age_seconds is None or age_seconds > config.max_stale_minutes * 60
    future_clock_skew = bool(age_seconds is not None and age_seconds < -300)
    healthy = bool(parsed.readings) and not stale and not future_clock_skew
    return BridgeHealthResult(
        status="ready" if healthy else "degraded",
        kind=config.kind,
        url=resolved_url,
        source=config.source,
        fetched_count=_payload_count(payload),
        parsed_count=len(parsed.readings),
        issue_count=len(parsed.issues),
        newest_reading_at=newest.isoformat() if newest else None,
        newest_reading_age_seconds=age_seconds,
        stale=stale,
        future_clock_skew=future_clock_skew,
        authenticated=bool(config.api_secret or config.access_token),
    )


def _payload_count(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("entries", "records", "sgv"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return len(rows)
        return 1
    return 0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0)
