from __future__ import annotations

from collections import Counter

from hermes_cgm_agent.domain import DataScope, GlucoseAggregate, GlucoseEvent
from hermes_cgm_agent.domain.report import (
    DataQualityWarning,
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    _aggregate_evidence,
    _coverage_confidence,
    _event_type_label,
)


class DailyCardMixin(BaseSectionMixin):
    def _daily_card_section(
        self,
        *,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        audience: ReportAudience,
        warnings: list[DataQualityWarning],
        detected_events: list[GlucoseEvent] | None = None,
        period_label: str = "今天",
    ) -> ReportSection:
        detected_events = detected_events or []
        card = self._daily_card_text(
            aggregate=aggregate,
            audience=audience,
            warnings=warnings,
            detected_events=detected_events,
        )
        # D056: the daily-card text opens with "今天/今日"; in a weekly/period
        # report that tense is wrong. Swap the leading temporal word once (both
        # are 2 chars and only appear at the start of these strings). Also give
        # the section a period-appropriate title.
        if period_label != "今天":
            if card.startswith(("今天", "今日")):
                card = period_label + card[2:]
        card_title = "日报卡片" if period_label == "今天" else "这段概况"
        return ReportSection(
            section_id="daily_card",
            kind="daily_card",
            title=card_title,
            content=card,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )

    def _daily_has_exception(
        self,
        *,
        aggregate: GlucoseAggregate,
        detected_events: list[GlucoseEvent],
        warnings: list[DataQualityWarning],
    ) -> bool:
        return bool(
            warnings
            or detected_events
            or aggregate.point_count == 0
            or (aggregate.tar or 0) > 0
            or (aggregate.tbr or 0) > 0
        )

    def _daily_card_text(
        self,
        *,
        aggregate: GlucoseAggregate,
        audience: ReportAudience,
        warnings: list[DataQualityWarning],
        detected_events: list[GlucoseEvent],
    ) -> str:
        if not self._daily_has_exception(
            aggregate=aggregate,
            detected_events=detected_events,
            warnings=warnings,
        ):
            if audience == ReportAudience.CLINICIAN:
                return f"今日整体平稳，TIR {aggregate.tir}%，数据覆盖率 {aggregate.data_coverage}%。"
            if audience == ReportAudience.FAMILY:
                return "今天整体平稳，没有看到需要特别担心的波动。"
            return "今天整体平稳，曲线大多顺着走，暂时没有看到特别突出的波动。"

        if aggregate.point_count == 0:
            if audience == ReportAudience.CLINICIAN:
                return "今日缺少有效 CGM 数据，本次日报仅能提示记录不足。"
            if audience == ReportAudience.FAMILY:
                return "今天主要是记录不够完整，先别急着往异常上想。"
            return "今天更像是数据没记全，先不急着下判断，等后面补上再一起看。"

        if (aggregate.tbr or 0) > 0:
            if audience == ReportAudience.CLINICIAN:
                return f"今日存在低于目标范围时间，TBR {aggregate.tbr}%，结合具体时段会更容易解释。"
            if audience == ReportAudience.FAMILY:
                return "今天有一小段偏低，不过已经被记录下来，可以安心回看。"
            return "今天有一小段偏低，看起来像某个时段短暂滑下去，可能和当时节奏有关。"

        if detected_events:
            dominant_type, dominant_count = Counter(
                str(event.event_type) for event in detected_events
            ).most_common(1)[0]
            label = _event_type_label(dominant_type, audience)
            if audience == ReportAudience.CLINICIAN:
                return f"今日以{label}为主，共检出 {dominant_count} 次，需结合餐后与活动时段判断。"
            if audience == ReportAudience.FAMILY:
                return f"今天有几段{label}，已经整理出来，先知道有这个变化就够了。"
            return f"今天有几段{label}，看起来像某个时段起伏更明显，可能和当时吃饭或活动有关。"

        if audience == ReportAudience.CLINICIAN:
            return f"今日偏高时间占比 {aggregate.tar}%，整体以高于目标范围暴露为主。"
        if audience == ReportAudience.FAMILY:
            return "今天有一点偏高的小起伏，不过整体脉络还是看得清。"
        return "今天有一点往高处走的小高峰，看起来可能跟当天吃饭节奏有关。"
