from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucoseEvent,
    GlucoseEventSeverity,
    GlucoseEventType,
    GlucosePoint,
)


@dataclass(frozen=True)
class EventDetectionConfig:
    """Deterministic thresholds for glucose event detection.

    All thresholds operate on mg/dL and on the normalized, valid-only point
    stream. Detection never uses an LLM (DECISION_LOG D015/D022).
    """

    low_threshold_mg_dl: float = 70
    high_threshold_mg_dl: float = 180
    # An episode must persist for at least this long to be reported.
    min_episode_minutes: float = 15
    # Rate-of-change episodes: mg/dL per minute over a short window.
    rapid_rate_mg_dl_per_min: float = 3.0
    rapid_window_minutes: float = 30
    # A gap longer than expected_interval * gap_factor counts as a data gap.
    expected_interval_minutes: float = 5
    gap_factor: float = 4.0
    # Severity escalation thresholds for hypo/hyper episodes.
    hypo_alert_threshold_mg_dl: float = 54
    hyper_alert_threshold_mg_dl: float = 250
    included_quality_flags: tuple[str, ...] = ("valid",)
    # Local hours [start, end) considered "overnight" for overnight-low tagging.
    overnight_start_hour: int = 0
    overnight_end_hour: int = 6
    timezone: str = "Asia/Shanghai"


class GlucoseEventDetector:
    def __init__(self, config: EventDetectionConfig | None = None) -> None:
        self.config = config or EventDetectionConfig()

    def detect(
        self,
        *,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucoseEvent]:
        eligible = self._eligible_points(points, scope)
        eligible.sort(key=lambda point: point.timestamp)
        events: list[GlucoseEvent] = []
        events.extend(self._detect_threshold_episodes(eligible, scope))
        events.extend(self._detect_rate_episodes(eligible, scope))
        events.extend(self._detect_data_gaps(eligible, scope))
        events.sort(key=lambda event: (event.ts_start, event.event_type))
        return events

    def _eligible_points(
        self,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucosePoint]:
        return [
            point
            for point in points
            if point.user_id == scope.user_id
            and scope.window_start <= point.timestamp < scope.window_end
            and (scope.source is None or point.source == scope.source)
            and str(point.quality_flag) in self.config.included_quality_flags
        ]

    def _detect_threshold_episodes(
        self,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucoseEvent]:
        events: list[GlucoseEvent] = []
        for event_type, predicate in (
            (GlucoseEventType.HYPO, lambda value: value < self.config.low_threshold_mg_dl),
            (GlucoseEventType.HYPER, lambda value: value > self.config.high_threshold_mg_dl),
        ):
            for run in _consecutive_runs(points, predicate):
                event = self._build_threshold_event(event_type, run, scope)
                if event is not None:
                    events.append(event)
        return events

    def _build_threshold_event(
        self,
        event_type: GlucoseEventType,
        run: list[GlucosePoint],
        scope: DataScope,
    ) -> GlucoseEvent | None:
        ts_start = run[0].timestamp
        ts_end = run[-1].timestamp
        duration_minutes = (ts_end - ts_start).total_seconds() / 60
        # C5: enforce the configured minimum episode duration. Gate on the
        # inclusive sample span (each reading covers ~one interval), so a single
        # point (covered = one interval) or a 2-point/5-min blip (covered 10min)
        # is suppressed, while a 3-point/10-min episode (covered 15min) is kept.
        # The previous `and len(run) < 2` made min_episode_minutes a no-op for
        # any run of >=2 points, emitting false hypo/hyper events.
        covered_minutes = duration_minutes + self.config.expected_interval_minutes
        if covered_minutes < self.config.min_episode_minutes:
            return None
        values = [point.value_mg_dl for point in run]
        nadir = min(values)
        peak = max(values)
        resolved_type = event_type
        if event_type == GlucoseEventType.HYPO and self._is_overnight(run[0]):
            resolved_type = GlucoseEventType.OVERNIGHT_LOW
        severity = self._threshold_severity(event_type, nadir=nadir, peak=peak)
        if event_type == GlucoseEventType.HYPO:
            summary = (
                f"Low glucose episode: nadir {nadir} mg/dL for "
                f"{round(duration_minutes)} min."
            )
        else:
            summary = (
                f"High glucose episode: peak {peak} mg/dL for "
                f"{round(duration_minutes)} min."
            )
        return GlucoseEvent(
            event_id=_event_id(scope.user_id, resolved_type.value, ts_start, ts_end),
            user_id=scope.user_id,
            event_type=resolved_type,
            ts_start=ts_start,
            ts_end=ts_end,
            severity=severity,
            peak_value_mg_dl=peak if event_type == GlucoseEventType.HYPER else None,
            nadir_value_mg_dl=nadir if event_type != GlucoseEventType.HYPER else None,
            duration_minutes=round(duration_minutes, 2),
            point_count=len(run),
            summary=summary,
            evidence_refs=[_point_evidence(point) for point in run],
        )

    def _threshold_severity(
        self,
        event_type: GlucoseEventType,
        *,
        nadir: float,
        peak: float,
    ) -> GlucoseEventSeverity:
        if event_type == GlucoseEventType.HYPO:
            if nadir <= self.config.hypo_alert_threshold_mg_dl:
                return GlucoseEventSeverity.ALERT
            return GlucoseEventSeverity.WARNING
        if peak >= self.config.hyper_alert_threshold_mg_dl:
            return GlucoseEventSeverity.ALERT
        return GlucoseEventSeverity.WARNING

    def _detect_rate_episodes(
        self,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucoseEvent]:
        events: list[GlucoseEvent] = []
        window = timedelta(minutes=self.config.rapid_window_minutes)
        for index, start_point in enumerate(points):
            for end_point in points[index + 1 :]:
                delta_minutes = (end_point.timestamp - start_point.timestamp).total_seconds() / 60
                if delta_minutes <= 0:
                    continue
                if end_point.timestamp - start_point.timestamp > window:
                    break
                rate = (end_point.value_mg_dl - start_point.value_mg_dl) / delta_minutes
                if rate >= self.config.rapid_rate_mg_dl_per_min:
                    events.append(
                        self._build_rate_event(
                            GlucoseEventType.RAPID_RISE, start_point, end_point, rate, scope
                        )
                    )
                    break
                if rate <= -self.config.rapid_rate_mg_dl_per_min:
                    events.append(
                        self._build_rate_event(
                            GlucoseEventType.RAPID_FALL, start_point, end_point, rate, scope
                        )
                    )
                    break
        return _dedupe_overlapping(events)

    def _build_rate_event(
        self,
        event_type: GlucoseEventType,
        start_point: GlucosePoint,
        end_point: GlucosePoint,
        rate: float,
        scope: DataScope,
    ) -> GlucoseEvent:
        duration_minutes = (end_point.timestamp - start_point.timestamp).total_seconds() / 60
        direction = "rise" if event_type == GlucoseEventType.RAPID_RISE else "fall"
        summary = (
            f"Rapid {direction}: {round(abs(rate), 2)} mg/dL per min "
            f"({start_point.value_mg_dl} -> {end_point.value_mg_dl} mg/dL)."
        )
        return GlucoseEvent(
            event_id=_event_id(
                scope.user_id, event_type.value, start_point.timestamp, end_point.timestamp
            ),
            user_id=scope.user_id,
            event_type=event_type,
            ts_start=start_point.timestamp,
            ts_end=end_point.timestamp,
            severity=GlucoseEventSeverity.INFO,
            peak_value_mg_dl=max(start_point.value_mg_dl, end_point.value_mg_dl),
            nadir_value_mg_dl=min(start_point.value_mg_dl, end_point.value_mg_dl),
            duration_minutes=round(duration_minutes, 2),
            point_count=2,
            summary=summary,
            evidence_refs=[_point_evidence(start_point), _point_evidence(end_point)],
        )

    def _detect_data_gaps(
        self,
        points: list[GlucosePoint],
        scope: DataScope,
    ) -> list[GlucoseEvent]:
        events: list[GlucoseEvent] = []
        max_gap = timedelta(
            minutes=self.config.expected_interval_minutes * self.config.gap_factor
        )
        for previous, current in zip(points, points[1:]):
            gap = current.timestamp - previous.timestamp
            if gap > max_gap:
                duration_minutes = gap.total_seconds() / 60
                events.append(
                    GlucoseEvent(
                        event_id=_event_id(
                            scope.user_id,
                            GlucoseEventType.DATA_GAP.value,
                            previous.timestamp,
                            current.timestamp,
                        ),
                        user_id=scope.user_id,
                        event_type=GlucoseEventType.DATA_GAP,
                        ts_start=previous.timestamp,
                        ts_end=current.timestamp,
                        severity=GlucoseEventSeverity.INFO,
                        duration_minutes=round(duration_minutes, 2),
                        point_count=0,
                        summary=(
                            f"Data gap of {round(duration_minutes)} min between valid points."
                        ),
                        evidence_refs=[
                            _point_evidence(previous),
                            _point_evidence(current),
                        ],
                    )
                )
        return events

    def _is_overnight(self, point: GlucosePoint) -> bool:
        local_hour = point.timestamp.astimezone(ZoneInfo(self.config.timezone)).hour
        return self.config.overnight_start_hour <= local_hour < self.config.overnight_end_hour


def _consecutive_runs(points, predicate):
    run: list = []
    for point in points:
        if predicate(point.value_mg_dl):
            run.append(point)
        elif run:
            yield run
            run = []
    if run:
        yield run


def _dedupe_overlapping(events: list[GlucoseEvent]) -> list[GlucoseEvent]:
    """Keep at most one rate event per overlapping window per type, earliest first."""
    kept: list[GlucoseEvent] = []
    for event in sorted(events, key=lambda item: (item.event_type, item.ts_start)):
        if kept and kept[-1].event_type == event.event_type and event.ts_start < kept[-1].ts_end:
            continue
        kept.append(event)
    return kept


def _point_evidence(point: GlucosePoint) -> EvidenceRef:
    return EvidenceRef(
        kind="glucose_point",
        ref_id=f"{point.user_id}:{point.timestamp.isoformat()}:{point.source}",
        summary=f"{point.timestamp.isoformat()} {point.value_mg_dl} mg/dL",
    )


def _event_id(user_id: str, event_type: str, ts_start, ts_end) -> str:
    raw = f"{user_id}:{event_type}:{ts_start.isoformat()}:{ts_end.isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
