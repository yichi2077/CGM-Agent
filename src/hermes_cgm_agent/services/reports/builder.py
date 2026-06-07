from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from collections import Counter

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    UserEvent,
)
from hermes_cgm_agent.domain.report import (
    AuthoritativeDocument,
    AuthoritativeContext,
    DataQualityWarning,
    DataQualitySeverity,
    G8MemoryCandidate,
    MemoryContext,
    Report,
    ReportAudience,
    ReportInput,
    ReportSection,
    ReportSourceTrack,
    ReportType,
)
from hermes_cgm_agent.services.analytics import (
    CGMAnalyticsService,
    GlucoseEventDetector,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.reports.renderer import render_markdown
from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository
from hermes_cgm_agent.services.safety import SafetyRouter


REPORT_WINDOW_DAYS = {
    ReportType.DAILY: 1,
    ReportType.WEEKLY: 7,
    ReportType.DOCTOR: 14,
}


@dataclass(frozen=True)
class PatternSignal:
    summaries: list[str]
    evidence_refs: list[EvidenceRef]
    emit_memory_candidates: bool


class ReportService:
    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        report_repository: SQLiteReportRepository,
        analytics_service: CGMAnalyticsService | None = None,
        event_detector: GlucoseEventDetector | None = None,
        safety_router: SafetyRouter | None = None,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.report_repository = report_repository
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector()
        self.safety_router = safety_router or SafetyRouter()

    def generate(self, report_input: ReportInput) -> Report:
        report_type = ReportType(report_input.report_type)
        scope = report_input.data_scope or resolve_report_scope(
            user_id=report_input.user_id or "",
            report_type=report_type,
            timezone_name=report_input.timezone,
            anchor_time=report_input.report_anchor_time,
            anchor_at=report_input.anchor_at,
        )
        report_id = uuid.uuid4().hex
        points = self.cgm_repository.list_glucose_points(scope)
        aggregate = self.analytics_service.compute_aggregate(
            points=points,
            scope=scope,
            window_label=_window_label(report_type),
        )
        events = self.cgm_repository.list_user_events(scope, include_rejected=False)
        if not report_input.include_candidate_events:
            events = [event for event in events if event.user_confirmed]
        detected_events = self.event_detector.detect(points=points, scope=scope)
        warnings = self._data_quality_warnings(points=points, aggregate=aggregate)
        safety_decision = self.safety_router.evaluate(scope=scope, points=points)
        if safety_decision.safety_result["status"] == "red_zone":
            sections = [
                ReportSection(
                    section_id="safety_red_zone",
                    kind="safety",
                    title="Safety",
                    content=safety_decision.message or "",
                    data_scope=scope,
                    evidence_refs=safety_decision.evidence_refs or [],
                    source_tracks=[ReportSourceTrack.FACT],
                    confidence=1.0,
                    warnings=warnings,
                )
            ]
        else:
            sections = self._sections(
                report_id=report_id,
                report_input=report_input,
                scope=scope,
                aggregate=aggregate,
                events=events,
                detected_events=detected_events,
                warnings=warnings,
            )
            # 🟡 Yellow zone: prepend alert prefix to the first section
            if safety_decision.safety_result["status"] == "yellow_zone" and sections:
                alert_prefix = safety_decision.message or ""
                sections[0] = sections[0].model_copy(
                    update={"content": alert_prefix + "\n\n" + sections[0].content}
                )
        candidates = [
            candidate
            for section in sections
            for candidate in section.g8_memory_candidates
        ]
        evidence_refs = _unique_evidence_refs(
            ref for section in sections for ref in section.evidence_refs
        )
        report = Report(
            report_id=report_id,
            user_id=scope.user_id,
            report_type=report_type,
            audience=report_input.audience,
            data_scope=scope,
            timezone=report_input.timezone,
            report_anchor_time=report_input.report_anchor_time,
            sections=sections,
            evidence_refs=evidence_refs,
            data_quality_warnings=warnings,
            g8_memory_candidates=candidates,
            source_versions={
                "report_contract": "G7",
                "analytics": "g7-analytics-v2",
                "event_detector": "g6-detector-v1",
                "memory_context": _context_version(report_input.memory_context),
                "authoritative_context": _context_version(report_input.authoritative_context),
            },
            route=safety_decision.route,
            safety_result=safety_decision.safety_result,
        )
        report.rendered_markdown = render_markdown(report)
        report.output_hash = _output_hash(report.rendered_markdown)
        return self.report_repository.create_report(report)

    def _sections(
        self,
        *,
        report_id: str,
        report_input: ReportInput,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        warnings: list[DataQualityWarning],
    ) -> list[ReportSection]:
        audience = ReportAudience(report_input.audience)
        report_type = ReportType(report_input.report_type)
        if report_type == ReportType.DAILY and not self._daily_has_exception(
            aggregate=aggregate,
            detected_events=detected_events,
            warnings=warnings,
        ):
            return [
                self._daily_card_section(
                    scope=scope,
                    aggregate=aggregate,
                    audience=audience,
                    warnings=warnings,
                )
            ]

        sections = [
            self._daily_card_section(
                scope=scope,
                aggregate=aggregate,
                audience=audience,
                warnings=warnings,
                detected_events=detected_events,
            ),
            self._overview_section(scope, aggregate, warnings, audience),
            self._metrics_section(scope, aggregate, audience),
            self._data_quality_section(scope, warnings, audience),
            self._key_events_section(report_id, scope, events, audience),
            self._detected_events_section(scope, detected_events, audience),
            self._observations_section(
                scope,
                aggregate,
                report_input.memory_context,
                report_input.authoritative_context,
                audience,
            ),
            self._follow_up_section(scope, aggregate, events, audience),
        ]
        if report_type == ReportType.WEEKLY:
            sections.append(
                self._patterns_section(report_id, scope, aggregate, events, detected_events, audience)
            )
        if report_type == ReportType.DOCTOR:
            sections.append(
                self._doctor_appendix_section(scope, aggregate, events, detected_events, warnings, audience)
            )
        return sections

    def _daily_card_section(
        self,
        *,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        audience: ReportAudience,
        warnings: list[DataQualityWarning],
        detected_events: list[GlucoseEvent] | None = None,
    ) -> ReportSection:
        detected_events = detected_events or []
        card = self._daily_card_text(
            aggregate=aggregate,
            audience=audience,
            warnings=warnings,
            detected_events=detected_events,
        )
        return ReportSection(
            section_id="daily_card",
            kind="daily_card",
            title="日报卡片",
            content=card,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=warnings,
        )

    def _overview_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        warnings: list[DataQualityWarning],
        audience: ReportAudience,
    ) -> ReportSection:
        if audience == ReportAudience.CLINICIAN:
            content = (
                f"本次覆盖 {scope.window_start.isoformat()} 至 {scope.window_end.isoformat()}，"
                f"纳入 {aggregate.point_count} 个有效 CGM 点，数据覆盖率 {aggregate.data_coverage}%。"
            )
            if warnings:
                content += " 合并数据质量说明，解读时需结合覆盖率一并判断。"
        elif audience == ReportAudience.FAMILY:
            content = (
                f"这段时间的数据大体够用，记录覆盖约 {aggregate.data_coverage}%，"
                "先按今天的整体走势来理解就可以。"
            )
        else:
            content = (
                f"这段时间一共记到 {aggregate.point_count} 个有效点，覆盖约 {aggregate.data_coverage}%。"
                "先把它当成今天生活节奏的一小段切片来看。"
            )
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
            content = (
                f"TIR {aggregate.tir}%，TAR {aggregate.tar}%，TBR {aggregate.tbr}%；"
                f"MBG {aggregate.mbg} mg/dL，CV {aggregate.cv}%，GMI {aggregate.gmi}。"
            )
        elif audience == ReportAudience.FAMILY:
            content = (
                f"大部分时间都在目标范围内，平均约 {aggregate.mbg} mg/dL，"
                "先看作今天整体还算有秩序。"
            )
        else:
            content = (
                f"大部分时间都在范围里，平均大约 {aggregate.mbg} mg/dL。"
                f"偏高约占 {aggregate.tar}%，偏低约占 {aggregate.tbr}%。"
            )
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

    def _key_events_section(
        self,
        report_id: str,
        scope: DataScope,
        events: list[UserEvent],
        audience: ReportAudience,
    ) -> ReportSection:
        confirmed = [event for event in events if event.user_confirmed]
        candidates = [event for event in events if not event.user_confirmed]
        evidence_refs = [_event_evidence(event) for event in events]
        memory_candidates = [
            G8MemoryCandidate(
                target_layer="L1",
                candidate_type="episode",
                summary=f"已确认一次{event.event_type}事件，时间在 {event.ts_start.isoformat()}。",
                source_report_id=report_id,
                source_section_id="key_events",
                evidence_refs=[_event_evidence(event)],
                confidence=event.confidence if event.confidence is not None else 0.7,
                requires_user_confirmation=False,
            )
            for event in confirmed
        ]
        if not events:
            if audience == ReportAudience.CLINICIAN:
                content = "本窗未记录用户事件，缺少餐食、运动、睡眠等外部时间锚点。"
            elif audience == ReportAudience.FAMILY:
                content = "今天没有额外备注事件，先按血糖走势本身理解。"
            else:
                content = "这段时间里还没有记下特别的生活事件，所以先只能结合曲线本身来看。"
        else:
            if audience == ReportAudience.CLINICIAN:
                content = f"用户事件共 {len(events)} 条，其中已确认 {len(confirmed)} 条，待核实 {len(candidates)} 条。"
            elif audience == ReportAudience.FAMILY:
                content = f"今天记了 {len(confirmed)} 件已确认的小事，先有个生活背景可以对照。"
            else:
                content = f"这段时间记下了 {len(confirmed)} 件已确认的小事，另外还有 {len(candidates)} 条待回想，拿来对照会更贴近当天情境。"
        return ReportSection(
            section_id="key_events",
            kind="key_events",
            title="生活事件",
            content=content,
            data_scope=scope,
            evidence_refs=evidence_refs,
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0 if not candidates else 0.8,
            g8_memory_candidates=memory_candidates,
        )

    def _detected_events_section(
        self,
        scope: DataScope,
        detected_events: list[GlucoseEvent],
        audience: ReportAudience,
    ) -> ReportSection:
        if not detected_events:
            if audience == ReportAudience.CLINICIAN:
                content = "本窗未检出系统定义的葡萄糖异常事件。"
            elif audience == ReportAudience.FAMILY:
                content = "系统这次没有抓到特别突出的波动片段。"
            else:
                content = "系统这次没抓到特别突出的波动片段，整体看起来还算顺着走。"
        else:
            counts = Counter(str(event.event_type) for event in detected_events)
            parts = "，".join(
                f"{_event_type_label(label, audience)} {count} 次"
                for label, count in sorted(counts.items())
            )
            if audience == ReportAudience.CLINICIAN:
                content = f"系统共检出 {len(detected_events)} 段葡萄糖事件：{parts}。"
            elif audience == ReportAudience.FAMILY:
                content = f"系统抓到 {len(detected_events)} 段波动，主要是{parts}，已经整理在这里。"
            else:
                content = f"系统抓到 {len(detected_events)} 段波动，主要是{parts}，看起来像今天起伏比较集中的那几段。"
        return ReportSection(
            section_id="detected_events",
            kind="detected_events",
            title="波动片段",
            content=content,
            data_scope=scope,
            evidence_refs=[
                ref for event in detected_events for ref in event.evidence_refs
            ],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=1.0,
        )

    def _observations_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        memory_context: MemoryContext,
        authoritative_context: AuthoritativeContext,
        audience: ReportAudience,
    ) -> ReportSection:
        observations = []
        section_warnings: list[DataQualityWarning] = []
        if aggregate.point_count == 0:
            if audience == ReportAudience.CLINICIAN:
                observations.append("本窗无有效 CGM 数据，暂不具备趋势判断基础。")
            elif audience == ReportAudience.FAMILY:
                observations.append("这段时间暂时没有可用数据，先不往结论上靠。")
            else:
                observations.append("这段时间没有留下可用数据，所以先不急着往规律上靠。")
        elif (aggregate.tar or 0) > (aggregate.tbr or 0) and (aggregate.tar or 0) > 0:
            if audience == ReportAudience.CLINICIAN:
                observations.append("本窗以高于目标范围时间为主，偏高负担高于偏低负担。")
            elif audience == ReportAudience.FAMILY:
                observations.append("今天主要是偏高多一点，不过还在可回看的范围里。")
            else:
                observations.append("这段更像是偏高的时候多一点，可能和吃饭节奏或活动安排有些关系。")
        elif (aggregate.tbr or 0) > 0:
            if audience == ReportAudience.CLINICIAN:
                observations.append("本窗出现低于目标范围时间，需结合具体时段解释。")
            elif audience == ReportAudience.FAMILY:
                observations.append("今天有一小段偏低，把当时前后发生的事一起放进来看会更清楚。")
            else:
                observations.append("这段里有一小段偏低，看起来可能和当时的进食或活动前后有关。")
        else:
            if audience == ReportAudience.CLINICIAN:
                observations.append("有效数据大多位于目标范围内，整体波动负担较轻。")
            elif audience == ReportAudience.FAMILY:
                observations.append("今天大多数时间都挺平稳，可以先放心。")
            else:
                observations.append("这段大多数时候都在范围里，整体看起来比较平顺。")

        source_tracks = [ReportSourceTrack.FACT]
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)]
        memory_refs = _context_evidence_refs(memory_context.items)
        authoritative_refs = _context_evidence_refs(authoritative_context.documents)
        if memory_refs:
            source_tracks.append(ReportSourceTrack.USER_MEMORY)
            evidence_refs.extend(memory_refs)
            observations.append(
                "这次也带上了过往记录，看看它和今天有没有能对得上的地方。"
                if audience != ReportAudience.CLINICIAN
                else "已合并既往记忆线索，用于辅助解释当前模式。"
            )
        if authoritative_refs:
            source_tracks.append(ReportSourceTrack.AUTHORITATIVE)
            evidence_refs.extend(authoritative_refs)
            observations.append(
                "也放进了参考资料，但它更像背景，不会替代你自己的记录。"
                if audience != ReportAudience.CLINICIAN
                else "已合并参考资料线索，用于补充背景解释。"
            )
            section_warnings.extend(_authoritative_context_warnings(authoritative_context.documents))
        if len(source_tracks) > 1:
            source_tracks.append(ReportSourceTrack.MIXED)

        return ReportSection(
            section_id="observations",
            kind="observations",
            title="观察",
            content=" ".join(observations),
            data_scope=scope,
            evidence_refs=evidence_refs,
            source_tracks=_unique_source_tracks(source_tracks),
            confidence=_coverage_confidence(aggregate.data_coverage),
            warnings=section_warnings,
        )

    def _follow_up_section(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        audience: ReportAudience,
    ) -> ReportSection:
        prompts = []
        if any(not event.user_confirmed for event in events):
            prompts.append(
                "有几条待核实的事件留在这里，回头想起时补一句，之后对照会更准。"
                if audience != ReportAudience.CLINICIAN
                else "存在待核实事件，后续若补全确认状态，归因解释会更完整。"
            )
        if aggregate.point_count == 0 or aggregate.data_coverage < 70:
            prompts.append(
                "这段里有些记录空白，像传感器间隙或暖机期，先记在心里就够了。"
                if audience != ReportAudience.CLINICIAN
                else "记录存在缺口，需结合传感器暖机、脱落或遗漏记录解释。"
            )
        if not events:
            prompts.append(
                "如果刚好记得那时吃了什么、动了多少，之后再补进来会更容易看出脉络。"
                if audience != ReportAudience.CLINICIAN
                else "若能补充餐食、运动、睡眠事件，可提升归因解释度。"
            )
        return ReportSection(
            section_id="follow_up_prompts",
            kind="follow_up_prompts",
            title="后续线索",
            content=" ".join(prompts) if prompts else (
                "目前没有额外需要补充的线索。"
                if audience != ReportAudience.CLINICIAN
                else "当前无额外待补充线索。"
            ),
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=0.8,
        )

    def _patterns_section(
        self,
        report_id: str,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
        detected_events: list[GlucoseEvent],
        audience: ReportAudience,
    ) -> ReportSection:
        evidence_refs = [_aggregate_evidence(scope, aggregate.window_label)] + [
            _event_evidence(event) for event in events if event.user_confirmed
        ]
        repeated = self._repeated_event_patterns(detected_events)
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
        timezone_name: str = "Asia/Shanghai",
    ) -> list[tuple[str, int]]:
        local_zone = ZoneInfo(timezone_name)
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
                f"平均 {aggregate.mbg} mg/dL，波动系数 {aggregate.cv}%。"
            )
        else:
            content = (
                f"结构化摘要：TIR={aggregate.tir}%，TAR={aggregate.tar}%，TBR={aggregate.tbr}%，"
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

    def _data_quality_warnings(
        self,
        *,
        points: list[GlucosePoint],
        aggregate: GlucoseAggregate,
    ) -> list[DataQualityWarning]:
        warnings: list[DataQualityWarning] = []
        aggregate_ref = _aggregate_evidence(
            DataScope(
                user_id=aggregate.user_id,
                window_start=aggregate.window_start,
                window_end=aggregate.window_end,
            ),
            aggregate.window_label,
        )
        if aggregate.point_count == 0:
            warnings.append(
                DataQualityWarning(
                    code="no_valid_points",
                    message="这段时间没有可用的 CGM 数据。",
                    severity="warning",
                    evidence_refs=[aggregate_ref],
                )
            )
        elif aggregate.data_coverage < 70:
            warnings.append(
                DataQualityWarning(
                    code="low_coverage",
                    message=f"数据覆盖率约 {aggregate.data_coverage}%，这段解读需要更保守一些。",
                    severity="warning",
                    evidence_refs=[aggregate_ref],
                )
            )
        non_valid_count = len([point for point in points if str(point.quality_flag) != "valid"])
        if non_valid_count:
            warnings.append(
                DataQualityWarning(
                    code="non_valid_points_present",
                    message=f"有 {non_valid_count} 个质量不稳定的数据点没有纳入指标计算。",
                    severity="info",
                    evidence_refs=[aggregate_ref],
                )
            )
        return warnings


def resolve_report_scope(
    *,
    user_id: str,
    report_type: ReportType | str,
    timezone_name: str = "Asia/Shanghai",
    anchor_time: time = time(7, 0),
    anchor_at: datetime | None = None,
) -> DataScope:
    parsed_type = ReportType(report_type)
    local_zone = ZoneInfo(timezone_name)
    now = anchor_at or datetime.now(timezone.utc)
    local_now = now.astimezone(local_zone)
    local_anchor = local_now.replace(
        hour=anchor_time.hour,
        minute=anchor_time.minute,
        second=anchor_time.second,
        microsecond=0,
    )
    if local_now < local_anchor:
        local_anchor = local_anchor - timedelta(days=1)
    window_end = local_anchor.astimezone(timezone.utc)
    window_start = window_end - timedelta(days=REPORT_WINDOW_DAYS[parsed_type])
    return DataScope(
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
    )


def _window_label(report_type: ReportType | str) -> str:
    report_type = ReportType(report_type)
    if report_type == ReportType.DAILY:
        return "day"
    if report_type == ReportType.WEEKLY:
        return "week"
    if report_type == ReportType.DOCTOR:
        return "14d"
    return report_type.value


def _aggregate_evidence(scope: DataScope, window_label: object | None) -> EvidenceRef:
    label = str(window_label or "window")
    return EvidenceRef(
        kind="aggregate",
        ref_id=f"{scope.user_id}:{scope.window_start.isoformat()}:{scope.window_end.isoformat()}:{label}",
        summary=f"{label} aggregate for {scope.window_start.isoformat()} to {scope.window_end.isoformat()}",
    )


def _event_evidence(event: UserEvent) -> EvidenceRef:
    state = "confirmed" if event.user_confirmed else "candidate"
    return EvidenceRef(
        kind="event",
        ref_id=event.event_id,
        summary=f"{state}: {event.event_type} at {event.ts_start.isoformat()}",
    )


def _coverage_confidence(data_coverage: float) -> float:
    if data_coverage >= 70:
        return 0.9
    if data_coverage > 0:
        return 0.55
    return 0.25


def _context_version(context: MemoryContext | AuthoritativeContext) -> str:
    if not context.enabled:
        return "disabled"
    if getattr(context, "missing_reason", None):
        return str(context.missing_reason)
    return "supplied" if (context.items if isinstance(context, MemoryContext) else context.documents) else "empty"


def _context_evidence_refs(items: list[dict[str, object] | AuthoritativeDocument]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in items:
        if isinstance(item, dict):
            raw_refs = item.get("evidence_refs", [])
        else:
            raw_refs = item.evidence_refs
        for ref in raw_refs:
            refs.append(EvidenceRef.model_validate(ref))
    return refs


def _authoritative_context_warnings(
    documents: list[AuthoritativeDocument],
) -> list[DataQualityWarning]:
    unverified = [doc for doc in documents if doc.verified is False]
    if not unverified:
        return []
    details = "；".join(_authoritative_doc_label(doc) for doc in unverified)
    return [
        DataQualityWarning(
            code="authoritative_unverified",
            severity=DataQualitySeverity.WARNING,
            message=(
                "以下为指南摘录草稿，非医疗建议；以下医学参考仍待人工核验，"
                "仅可作为背景线索，不能作为最终医学依据："
                f"{details}"
            ),
            evidence_refs=_context_evidence_refs(unverified),
        )
    ]


def _authoritative_doc_label(doc: AuthoritativeDocument) -> str:
    label = doc.title
    if doc.population:
        label += f" [{doc.population}]"
    if doc.source:
        label += f" ({doc.source})"
    return label


def _unique_evidence_refs(refs: object) -> list[EvidenceRef]:
    unique: dict[tuple[str, str], EvidenceRef] = {}
    for ref in refs:
        parsed = EvidenceRef.model_validate(ref)
        unique[(str(parsed.kind), parsed.ref_id)] = parsed
    return list(unique.values())


def _unique_source_tracks(tracks: list[ReportSourceTrack]) -> list[ReportSourceTrack]:
    return list(dict.fromkeys(tracks))


def _output_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def _event_type_label(event_type: str, audience: ReportAudience) -> str:
    labels = {
        "hypo": ("偏低片段", "低血糖事件", "偏低片段"),
        "hyper": ("偏高片段", "高血糖事件", "偏高片段"),
        "rapid_rise": ("上冲片段", "快速上升事件", "上冲片段"),
        "rapid_fall": ("回落片段", "快速下降事件", "回落片段"),
        "overnight_low": ("夜间偏低片段", "夜间低血糖事件", "夜间偏低片段"),
        "data_gap": ("记录空白片段", "数据缺口事件", "记录空白片段"),
    }
    self_label, clinician_label, family_label = labels.get(
        event_type,
        (event_type.replace("_", " "), event_type.replace("_", " "), event_type.replace("_", " ")),
    )
    if audience == ReportAudience.CLINICIAN:
        return clinician_label
    if audience == ReportAudience.FAMILY:
        return family_label
    return self_label
