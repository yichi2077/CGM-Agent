from __future__ import annotations

from hermes_cgm_agent.domain import DataScope, GlucoseAggregate
from hermes_cgm_agent.domain.report import (
    DataQualityWarning,
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.narrative_templates import translate_metric
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    _aggregate_evidence,
    _coverage_confidence,
    _display_glucose,
)


class MetricsMixin(BaseSectionMixin):
    def _overview_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        warnings: list[DataQualityWarning],
        audience: ReportAudience,
        period_label: str = "这段时间",
    ) -> ReportSection:
        if audience == ReportAudience.CLINICIAN:
            content = (
                f"本次覆盖 {scope.window_start.isoformat()} 至 {scope.window_end.isoformat()}，"
                f"纳入 {aggregate.point_count} 个有效 CGM 点，数据覆盖率 {aggregate.data_coverage}%。"
            )
            if warnings:
                content += " 合并数据质量说明，解读时需结合覆盖率一并判断。"
        else:
            # Everyday / family: describe completeness in plain words (D056) —
            # never "N 个有效点 / 覆盖 X%", which reads as engineering telemetry.
            if aggregate.data_coverage >= 70:
                content = f"{period_label}的记录挺完整的，下面的情况可以比较放心地看。"
            else:
                content = f"{period_label}有一部分时间没有记到数据，下面的情况先作个参考。"
        return ReportSection(
            section_id="overview",
            kind="overview",
            title="整体概览",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )

    def _metrics_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        audience: ReportAudience,
    ) -> ReportSection:
        if aggregate.point_count == 0:
            if audience == ReportAudience.CLINICIAN:
                content = "本窗暂无可计算的关键指标；TIR/TAR/TBR、MBG、CV 与 GMI 均需有效 CGM 数据后再解读。"
            elif audience == ReportAudience.FAMILY:
                content = "这段时间暂无可计算的关键指标，先等数据补齐后再看平均值和偏高偏低比例。"
            else:
                content = "这段时间暂无可计算的关键指标，先不看平均值、偏高比例或偏低比例。"
        elif audience == ReportAudience.CLINICIAN:
            # AGP 2019-consensus 5-level split (D055): TBR/TAR are TOTALs; the
            # very-low (<54) and very-high (>250) Level-2 bands are called out
            # so severe-hypo/hyper burden is directly readable.
            content = (
                f"TIR {aggregate.tir}%，TAR {aggregate.tar}%（其中极高>250 {aggregate.tar_very_high}%），"
                f"TBR {aggregate.tbr}%（其中极低<54 {aggregate.tbr_very_low}%）；"
                f"MBG {aggregate.mbg} mg/dL，CV {aggregate.cv}%，GMI {aggregate.gmi}。"
            )
        elif audience == ReportAudience.FAMILY:
            # Family: reassurance-first, minimal numbers, hypo before hyper (a
            # low is what a caregiver most needs to know). D056: build the
            # sentence conditionally — never concatenate translate_metric
            # fragments (that produced "平均平均状态" / "没有偏高约占0%").
            tir_str = translate_metric("TIR", aggregate.tir, audience)
            high = aggregate.tar or 0
            low = aggregate.tbr or 0
            content = f"{tir_str}，平均血糖大约 {_display_glucose(aggregate.mbg)}。"
            if low == 0 and high == 0:
                content += "从整体看大方向挺平稳的。"
            elif low > 0:
                content += "偶尔有偏低的时候，平时可以多留意一下。"
            else:
                content += "偶尔有偏高的时候，整体问题不大。"
        else:
            tir_str = translate_metric("TIR", aggregate.tir, audience)
            high = aggregate.tar or 0
            low = aggregate.tbr or 0
            content = f"{tir_str}，平均血糖大约 {_display_glucose(aggregate.mbg)}。"
            if high == 0 and low == 0:
                # Metric-level statement only (D056): TBR/TAR here are window
                # totals; short individual events can still surface in
                # 波动片段/我们在一起观察的, so avoid an absolute "完全没有" that
                # reads as contradicting those sections.
                content += "从整体比例看，偏高和偏低的时间都很少，大方向挺稳的。"
            else:
                bits: list[str] = []
                if high > 0:
                    bits.append(f"有大约 {aggregate.tar}% 的时间偏高")
                if low > 0:
                    bits.append(f"有大约 {aggregate.tbr}% 的时间偏低")
                content += "，".join(bits) + "，可以多留意一下。"
        return ReportSection(
            section_id="metrics",
            kind="metrics",
            title="关键指标",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
        )

    def _data_quality_section(
        self,
        scope: DataScope,
        warnings: list[DataQualityWarning],
        audience: ReportAudience,
    ) -> ReportSection:
        if not warnings:
            if audience == ReportAudience.CLINICIAN:
                content = "本窗未见额外数据质量问题，指标可按当前覆盖率常规解读。"
            elif audience == ReportAudience.FAMILY:
                content = "这段记录基本完整，先不用为数据本身担心。"
            else:
                content = "这段数据记得还算完整，先按现在看到的走势来理解就行。"
        else:
            prefix = "数据质量说明：" if audience == ReportAudience.CLINICIAN else "这段数据里有些地方还不够完整："
            content = prefix + "；".join(warning.message for warning in warnings)
        return ReportSection(
            section_id="data_quality",
            kind="data_quality",
            title="数据质量说明",
            content=content,
            data_scope=scope,
            evidence_refs=[ref for warning in warnings for ref in warning.evidence_refs],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0,
            warnings=warnings,
        )
