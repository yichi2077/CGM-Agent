from __future__ import annotations

from hermes_cgm_agent.domain import DataScope, GlucoseAggregate, GlucoseEvent
from hermes_cgm_agent.domain.report import (
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.narrative_templates import (
    render_monthly_comparison,
    render_monthly_summary,
)
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    _aggregate_evidence,
    _coverage_confidence,
    _event_type_label,
)


class MonthlySectionsMixin(BaseSectionMixin):
    """G6: month-level narrative sections (summary + MoM comparison + patterns).

    Monthly stays at monthly altitude: trend direction against the previous
    month, recurring patterns across the month — never single-day drilldown
    (that belongs to daily/weekly reports).
    """

    def _previous_month_aggregate(
        self, scope: DataScope, aggregate: GlucoseAggregate
    ) -> GlucoseAggregate | None:
        """Aggregate for the 30-day window immediately before this report's."""
        span = scope.window_end - scope.window_start
        prev_scope = DataScope(
            user_id=scope.user_id,
            window_start=scope.window_start - span,
            window_end=scope.window_start,
        )
        prev_points = self.cgm_repository.list_glucose_points(prev_scope)
        if not prev_points:
            return None
        return self.analytics_service.compute_aggregate(
            points=prev_points,
            scope=prev_scope,
            window_label=aggregate.window_label,
        )

    def _monthly_summary_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        prev_aggregate: GlucoseAggregate | None,
        audience: ReportAudience,
    ) -> ReportSection:
        content = render_monthly_summary(aggregate, prev_aggregate, audience)
        comparison = render_monthly_comparison(aggregate, prev_aggregate, audience)
        return ReportSection(
            section_id="monthly_summary",
            kind="monthly_summary",
            title="本月概览" if audience != ReportAudience.CLINICIAN else "月度概览与环比",
            content=f"{content} {comparison}".strip(),
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
        )

    def _monthly_patterns_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        detected_events: list[GlucoseEvent],
        audience: ReportAudience,
        *,
        timezone_name: str | None = None,
    ) -> ReportSection:
        # Reuse the weekly repetition analysis with a month-appropriate bar:
        # a monthly pattern needs the same event type on 4+ distinct days.
        repeated = self._repeated_event_patterns(
            detected_events,
            min_days=4,
            timezone_name=timezone_name,
        )
        summaries: list[str] = []
        for event_type, day_count in repeated:
            label = _event_type_label(event_type, audience)
            summaries.append(
                f"这个月有 {day_count} 天出现类似的{label}，看起来像一个月度层面的线索。"
                if audience != ReportAudience.CLINICIAN
                else f"本月有 {day_count} 个不同日期出现重复的{label}事件。"
            )
        if not summaries:
            summaries.append(
                "这个月暂时没看到特别稳定的重复模式，先继续观察就好。"
                if audience != ReportAudience.CLINICIAN
                else "本月窗尚未形成稳定重复模式。"
            )
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)]
        return ReportSection(
            section_id="monthly_patterns",
            kind="monthly_patterns",
            title="月度模式" if audience != ReportAudience.CLINICIAN else "月度模式分析",
            content=" ".join(summaries),
            data_scope=scope,
            evidence_refs=evidence_refs,
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            omit_for_companion=not repeated,
        )
