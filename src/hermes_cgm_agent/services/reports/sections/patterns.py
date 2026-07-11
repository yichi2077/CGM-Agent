from __future__ import annotations

from zoneinfo import ZoneInfo

from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    UserEvent,
)
from hermes_cgm_agent.domain.report import (
    G8MemoryCandidate,
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.narrative_templates import render_hypothesis_narrative
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    PatternSignal,
    _aggregate_evidence,
    _coverage_confidence,
    _event_evidence,
    _event_type_label,
    _unique_evidence_refs,
)


class PatternsMixin(BaseSectionMixin):
    def _patterns_section(
        self,
        report_id: str,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        audience: ReportAudience,
        *,
        timezone_name: str | None = None,
    ) -> ReportSection:
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)] + [
            _event_evidence(event) for event in events if event.user_confirmed
        ]
        repeated = self._repeated_event_patterns(
            detected_events,
            timezone_name=timezone_name or default_timezone(),
        )
        signal = self._pattern_signal(
            aggregate=aggregate,
            repeated=repeated,
            detected_events=detected_events,
            audience=audience,
        )
        evidence_refs.extend(signal.evidence_refs)

        candidates = [
            G8MemoryCandidate(
                target_layer="L3",
                candidate_type="hypothesis",
                summary=summary,
                source_report_id=report_id,
                source_section_id="patterns",
                evidence_refs=_unique_evidence_refs(evidence_refs),
                confidence=_coverage_confidence(aggregate.data_coverage),
                requires_user_confirmation=True,
            )
            for summary in signal.summaries
            if signal.emit_memory_candidates
        ]
        return ReportSection(
            section_id="patterns",
            kind="patterns",
            title="模式线索",
            content=" ".join(signal.summaries),
            data_scope=scope,
            evidence_refs=_unique_evidence_refs(evidence_refs),
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            g8_memory_candidates=candidates,
            # D056: hide the empty "还没看到稳定模式" filler from everyday readers
            # (it contradicts "我们在一起观察的"); show it only when there is a
            # real cross-day recurring pattern in THIS window. Keyed on
            # `repeated` (not candidate emission, which can fire on a single
            # window's events) so the render decision matches what the text says.
            omit_for_companion=not repeated,
        )

    def _hypothesis_narrative_section(
        self,
        scope: DataScope,
        audience: ReportAudience,
        *,
        timezone_name: str | None = None,
    ) -> ReportSection | None:
        """US2/FR-004: render active L3 hypotheses in state-appropriate companion
        language (candidate/observing/stable/archived).

        Personal-track only — hypotheses are individualized inferences from the
        user's own data and MUST NOT be merged with the authoritative KB track
        (Principle II). Suppressed in red zone by construction (only built on the
        normal `_sections` path, never in the red-zone/disclaimer branches).
        """
        hypotheses = self.memory_repository.list_hypotheses(scope.user_id)
        rendered: list[str] = []
        evidence_refs: list[EvidenceRef] = []
        for hyp in hypotheses:
            if hyp.valid_to is not None:
                continue  # superseded / closed bi-temporal window
            rendered.append(
                render_hypothesis_narrative(hyp.state, hyp.statement, hyp.evidence_count)
            )
            evidence_refs.extend(hyp.evidence_refs)
        if not rendered:
            return None
        return ReportSection(
            section_id="hypothesis_narrative",
            kind="hypothesis_narrative",
            title="我们在一起观察的",
            content=" ".join(rendered[:3]),
            data_scope=scope,
            evidence_refs=_unique_evidence_refs(evidence_refs),
            source_tracks=[ReportSourceTrack.FACT],
            confidence=0.6,
        )

    def _pattern_signal(
        self,
        *,
        aggregate: GlucoseAggregate,
        repeated: list[tuple[str, int]],
        detected_events: list[GlucoseEvent],
        audience: ReportAudience,
    ) -> PatternSignal:
        if aggregate.point_count == 0:
            return PatternSignal(
                summaries=[
                    "尚无足够数据形成模式线索，先不沉淀为长期记忆。"
                    if audience != ReportAudience.CLINICIAN
                    else "本窗无有效 CGM 数据，尚无足够证据形成模式线索。"
                ],
                evidence_refs=[],
                emit_memory_candidates=False,
            )

        summaries: list[str] = []
        evidence_refs: list[EvidenceRef] = []
        # Repetition analysis over detected glucose events: a pattern needs the
        # same event type recurring on multiple distinct local days, not just a
        # single window-level aggregate threshold (audit P1-3 fix).
        for event_type, day_count in repeated:
            label = _event_type_label(event_type, audience)
            summaries.append(
                (
                    f"这周有 {day_count} 天出现类似的{label}，看起来可能有关，但还不够确定。"
                    if audience != ReportAudience.CLINICIAN
                    else f"本周有 {day_count} 个不同日期出现重复的{label}事件。"
                )
            )
            evidence_refs.extend(
                ref
                for event in detected_events
                if str(event.event_type) == event_type
                for ref in event.evidence_refs
            )

        if summaries:
            return PatternSignal(
                summaries=summaries,
                evidence_refs=evidence_refs,
                emit_memory_candidates=True,
            )
        if (aggregate.tar or 0) >= 20:
            summary = (
                "这周偏高的时间有点集中，看起来可能跟固定时段有关，但还不够确定。"
                if audience != ReportAudience.CLINICIAN
                else "本周高于目标范围时间占比升高，结合时段分层后会更容易解释。"
            )
        elif (aggregate.tbr or 0) >= 5:
            summary = (
                "这周有几段偏低反复出现，看起来像个线索，但还想再多看几次。"
                if audience != ReportAudience.CLINICIAN
                else "本周出现低于目标范围时间，结合具体时段与诱因复核会更稳妥。"
            )
        else:
            summary = (
                "这周暂时还没看到特别稳定的重复模式，先继续观察就好。"
                if audience != ReportAudience.CLINICIAN
                else "当前周窗尚未形成稳定重复模式，证据仍不足。"
            )
        return PatternSignal(summaries=[summary], evidence_refs=[], emit_memory_candidates=True)

    def _repeated_event_patterns(
        self,
        detected_events: list[GlucoseEvent],
        *,
        min_days: int = 2,
        timezone_name: str | None = None,
    ) -> list[tuple[str, int]]:
        local_zone = ZoneInfo(timezone_name or default_timezone())
        days_by_type: dict[str, set] = {}
        for event in detected_events:
            local_day = event.ts_start.astimezone(local_zone).date()
            days_by_type.setdefault(str(event.event_type), set()).add(local_day)
        repeated = [
            (event_type, len(days))
            for event_type, days in days_by_type.items()
            if len(days) >= min_days
        ]
        return sorted(repeated, key=lambda item: (-item[1], item[0]))
