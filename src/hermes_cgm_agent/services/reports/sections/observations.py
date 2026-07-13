from __future__ import annotations

from hermes_cgm_agent.domain import (
    DataScope,
    EscalationState,
    GlucoseAggregate,
    GlucoseEvent,
    GlucoseEventType,
    UserEvent,
)
from hermes_cgm_agent.domain.report import (
    AuthoritativeContext,
    DataQualityWarning,
    MemoryContext,
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    _aggregate_evidence,
    _authoritative_context_warnings,
    _context_evidence_refs,
    _coverage_confidence,
    _event_evidence,
    _unique_source_tracks,
)

# E2: a sentinel HTML comment prefixed to each RAG context-merge sentence so
# the companion renderer (builder.render_companion) can strip these standard
# additions from its tone/length validation by MARKER rather than by fragile
# exact-string matching. The marker itself is removed from every rendered
# output path (companion AND clinical) so it never reaches the reader; the
# merge sentence stays as user-facing context. Kept as a module constant so
# the producer (here) and consumer (builder) share one source of truth.
RAG_MERGE_MARKER = "<!--rag_merge-->"


class ObservationsMixin(BaseSectionMixin):
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
                observations.append("这段时间主要是偏高多一点，不过还在可回看的范围里。")
            else:
                observations.append("这段更像是偏高的时候多一点，可能和吃饭节奏或活动安排有些关系。")
        elif (aggregate.tbr or 0) > 0:
            if audience == ReportAudience.CLINICIAN:
                observations.append("本窗出现低于目标范围时间，需结合具体时段解释。")
            elif audience == ReportAudience.FAMILY:
                observations.append("这段时间有一小段偏低，把当时前后发生的事一起放进来看会更清楚。")
            else:
                observations.append("这段里有一小段偏低，看起来可能和当时的进食或活动前后有关。")
        else:
            if audience == ReportAudience.CLINICIAN:
                observations.append("有效数据大多位于目标范围内，整体波动负担较轻。")
            elif audience == ReportAudience.FAMILY:
                observations.append("这段时间大多数时间都挺平稳，可以先放心。")
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
                # E2: prefix the merge sentence with RAG_MERGE_MARKER so the
                # companion renderer strips it from validation by marker.
                f"{RAG_MERGE_MARKER}这次也带上了过往记录，看看它和今天有没有能对得上的地方。"
                if audience != ReportAudience.CLINICIAN
                else "已合并既往记忆线索，用于辅助解释当前模式。"
            )
        if authoritative_refs:
            source_tracks.append(ReportSourceTrack.AUTHORITATIVE)
            evidence_refs.extend(authoritative_refs)
            observations.append(
                f"{RAG_MERGE_MARKER}也放进了参考资料，但它更像背景，不会替代你自己的记录。"
                if audience != ReportAudience.CLINICIAN
                else "已合并参考资料线索，用于补充背景解释。"
            )
            section_warnings.extend(_authoritative_context_warnings(authoritative_context.documents))
        # D031: surface conflict arbitration gently — authoritative wins, but
        # the user's own record is acknowledged, never denied.
        if memory_context.conflict_resolutions:
            observations.append(
                f"{RAG_MERGE_MARKER}你记录里的血糖范围和参考资料的目标范围有些出入，"
                "这里以参考资料为准，不过你的记录同样值得留着慢慢对照。"
                if audience != ReportAudience.CLINICIAN
                else "既往个人记录与权威参考范围存在数值出入，本报告以权威范围为准（D031）。"
            )
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
        esc_state: EscalationState = EscalationState.NORMAL,
        consecutive_days: int = 0,
        detected_events: list[GlucoseEvent] | None = None,
    ) -> ReportSection:
        detected_events = detected_events or []
        if audience == ReportAudience.CLINICIAN:
            return self._follow_up_section_clinical(scope, aggregate, events)

        # Everyday / family (D057): lead with the RIGHT adherence hook, not a
        # chore list. Positive reinforcement when things go well is the single
        # strongest adherence driver — the user needs to feel seen and
        # successful. Escalation concern comes first (safety), then
        # reinforcement or gentle continuity; data-entry asks are secondary and
        # framed as benefiting the user, never as work for the system.
        tir = aggregate.tir if aggregate.tir is not None else 0.0
        has_anomaly = any(
            e.event_type != GlucoseEventType.DATA_GAP for e in detected_events
        )
        prompts: list[str] = []
        if esc_state == EscalationState.CONCERN:
            prompts.append("最近几天都有点波动，你还好吗？我一直在这儿。")
        elif esc_state == EscalationState.EXTERNAL_SUPPORT:
            prompts.append("这几天的情况，要不要下次复诊时也跟医生聊聊？我可以帮你把记录整理好。")

        if not prompts:  # not in an escalation state
            if tir >= 70 and not has_anomaly:
                prompts.append("这段时间整体保持得挺好，继续现在的节奏就好。")
            elif tir >= 70:
                prompts.append("大方向保持得不错，个别小波动我们慢慢看，不用急。")
            else:
                prompts.append("这段时间有点起伏，我们一步一步来，不用一次看太多。")

        if aggregate.point_count == 0 or aggregate.data_coverage < 70:
            prompts.append("这段里有些记录空白，可能是传感器间隙或暖机期，先不用在意。")
        elif not events:
            # gentle, user-benefit framing — offered once, never nagging
            prompts.append("要是哪天想起当时吃了什么、动了多少，随手记一笔，之后更容易看出属于你的规律。")

        content = " ".join(prompts)
        return ReportSection(
            section_id="follow_up_prompts",
            kind="follow_up_prompts",
            title="接下来",
            content=content,
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=0.8,
        )

    def _follow_up_section_clinical(
        self,
        scope: DataScope,
        aggregate: GlucoseAggregate,
        events: list[UserEvent],
    ) -> ReportSection:
        # Clinician follow-up stays clinical/attribution-oriented (D057): flag
        # unconfirmed events and data gaps that affect interpretation. No
        # companion-style encouragement — that belongs to the everyday report.
        prompts: list[str] = []
        if any(not event.user_confirmed for event in events):
            prompts.append("存在待核实事件，后续若补全确认状态，归因解释会更完整。")
        if aggregate.point_count == 0 or aggregate.data_coverage < 70:
            prompts.append("记录存在缺口，需结合传感器暖机、脱落或遗漏记录解释。")
        if not events:
            prompts.append("若能补充餐食、运动、睡眠事件，可提升归因解释度。")
        return ReportSection(
            section_id="follow_up_prompts",
            kind="follow_up_prompts",
            title="后续线索",
            content=" ".join(prompts) if prompts else "当前无额外待补充线索。",
            data_scope=scope,
            evidence_refs=[_aggregate_evidence(scope, aggregate.window_label)] + [_event_evidence(event) for event in events],
            source_tracks=[ReportSourceTrack.FACT],
            confidence=0.8,
        )
