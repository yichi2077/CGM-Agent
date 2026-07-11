from __future__ import annotations

from collections import Counter

from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.domain import DataScope, GlucoseEvent, GlucoseEventType, UserEvent
from hermes_cgm_agent.domain.report import (
    G8MemoryCandidate,
    ReportAudience,
    ReportSection,
    ReportSourceTrack,
)
from hermes_cgm_agent.services.reports.narrative_templates import render_meal_summary
from hermes_cgm_agent.services.reports.sections.base import BaseSectionMixin
from hermes_cgm_agent.services.reports.sections.helpers import (
    _event_evidence,
    _event_type_label,
)


class EventsMixin(BaseSectionMixin):
    def _key_events_section(
        self,
        report_id: str,
        scope: DataScope,
        events: list[UserEvent],
        audience: ReportAudience,
        *,
        timezone_name: str | None = None,
    ) -> ReportSection:
        confirmed = [event for event in events if event.user_confirmed]
        candidates = [event for event in events if not event.user_confirmed]
        evidence_refs = [_event_evidence(event) for event in events]

        # E3: render meal-type UserEvents as companion-tone narratives via
        # render_meal_summary instead of leaving it as dead code. The
        # narrative replaces the raw payload/type string in both the
        # section content and the G8 memory candidate summary.
        tz = timezone_name or default_timezone()

        def _event_candidate_summary(event: UserEvent) -> str:
            etype = getattr(event.event_type, "value", event.event_type)
            if etype == "meal":
                return render_meal_summary(event, timezone_name=tz)
            return f"已确认一次{etype}事件，时间在 {event.ts_start.isoformat()}。"

        memory_candidates = [
            G8MemoryCandidate(
                target_layer="L1",
                candidate_type="episode",
                summary=_event_candidate_summary(event),
                source_report_id=report_id,
                source_section_id="key_events",
                evidence_refs=[_event_evidence(event)],
                confidence=event.confidence if event.confidence is not None else 0.7,
                requires_user_confirmation=False,
            )
            for event in confirmed
        ]

        # E3: companion-tone meal narratives for non-clinician audiences.
        meal_narratives = [
            render_meal_summary(event, timezone_name=tz)
            for event in confirmed
            if getattr(event.event_type, "value", event.event_type) == "meal"
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
            # E3: prepend meal narratives for companion audiences so meal
            # events are referenced naturally ("中午吃了面条。") instead of
            # as a raw structured payload.
            # M-09: only prepend for non-clinician audiences; clinician
            # content must stay pure clinical text.
            if meal_narratives and audience != ReportAudience.CLINICIAN:
                content = " ".join(meal_narratives) + " " + content
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
            omit_for_companion=not events,
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
            # Everyday reader (D056): DATA_GAP-only is not a "波动片段" — gaps are
            # already covered by 数据质量说明. Show this section to companions
            # only when there is a real (non-gap) glucose event.
            omit_for_companion=not any(
                e.event_type != GlucoseEventType.DATA_GAP for e in detected_events
            ),
        )
