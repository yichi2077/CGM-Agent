from __future__ import annotations

from hermes_cgm_agent.domain import (
    DataScope,
    GlucoseAggregate,
    GlucoseEvent,
    UserEvent,
)
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
    _display_glucose,
    _event_evidence,
)


class DoctorMixin(BaseSectionMixin):
    def _doctor_appendix_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        warnings: list[DataQualityWarning],
        audience: ReportAudience,
    ) -> ReportSection:
        if audience == ReportAudience.FAMILY:
            content = "这份医生版附录主要是给门诊快速查看的数字摘要，家里先知道整体已整理好就可以。"
        elif audience == ReportAudience.SELF:
            content = (
                f"给医生快速扫读的数字版：TIR {aggregate.tir}%，TAR {aggregate.tar}%，TBR {aggregate.tbr}%，"
                f"平均 {_display_glucose(aggregate.mbg)}，波动系数 {aggregate.cv}%。"
            )
        else:
            content = (
                f"结构化摘要：TIR={aggregate.tir}%，TAR={aggregate.tar}%（极高>250 {aggregate.tar_very_high}%），"
                f"TBR={aggregate.tbr}%（极低<54 {aggregate.tbr_very_low}%），"
                f"MBG={aggregate.mbg} mg/dL，CV={aggregate.cv}%，GMI={aggregate.gmi}，"
                f"LBGI={aggregate.lbgi}，HBGI={aggregate.hbgi}，覆盖率={aggregate.data_coverage}%，"
                f"已确认事件={len([event for event in events if event.user_confirmed])}，"
                f"系统检出事件={len(detected_events)}，数据质量说明={len(warnings)}。"
            )
        return ReportSection(
            section_id="doctor_appendix",
            kind="doctor_appendix",
            title="医生附录",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )
