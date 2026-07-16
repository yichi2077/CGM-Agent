from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from hermes_cgm_agent.domain import (
    DataScope,
    EscalationState,
    GlucoseAggregate,
    GlucoseEvent,
    GlucosePoint,
    UserEvent,
)
from hermes_cgm_agent.domain.report import (
    DataQualityWarning,
    Report,
    ReportAudience,
    ReportInput,
    ReportSection,
    ReportSourceTrack,
    ReportType,
)
from hermes_cgm_agent.services.analytics import (
    AnalyticsConfig,
    CGMAnalyticsService,
    EventDetectionConfig,
    GlucoseEventDetector,
    median_interval_minutes,
)
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.affect import detect_affect
from hermes_cgm_agent.services.reports.renderer import (
    CITATION_BLOCK_TEMPLATE,
    MEDICAL_DISCLAIMER_FOOTER,
    render_markdown,
)
from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository
from hermes_cgm_agent.services.reports.sections.monthly import MonthlySectionsMixin
from hermes_cgm_agent.services.reports.sections import (
    DailyCardMixin,
    DoctorMixin,
    EventsMixin,
    MetricsMixin,
    ObservationsMixin,
    PatternsMixin,
)
from hermes_cgm_agent.services.reports.sections.helpers import (
    _aggregate_evidence,
    _context_version,
    _output_hash,
    _unique_evidence_refs,
    _window_label,
    resolve_report_scope,
)
# E2: shared sentinel for RAG context-merge sentences (produced in
# sections/observations.py). Imported here so the renderer's stripping logic
# and the producer share a single source of truth for the marker string.
from hermes_cgm_agent.services.reports.sections.observations import RAG_MERGE_MARKER
from hermes_cgm_agent.services.safety import SafetyRouter, assert_authoritative_quotes

# Re-exported for back-compat: callers import resolve_report_scope from this
# module (e.g. reports/__init__.py, reports/tools.py, simulation/runner.py).
__all__ = ["ReportService", "resolve_report_scope"]

# E2: matches a RAG context-merge marker plus the merge sentence that follows
# it, up to the next marker or end-of-string. render_companion uses this to
# strip these standard additions from companion tone/length validation by
# MARKER instead of fragile exact-string matching, so the merge wording can
# evolve without silently disabling the guard.
# L-07: \Z anchor is intentional — RAG merge segments are always appended at
# the end of the content, so stripping to end-of-string is correct behavior.
_RAG_MERGE_SEGMENT_RE = re.compile(
    rf"{re.escape(RAG_MERGE_MARKER)}.*?(?={re.escape(RAG_MERGE_MARKER)}|\Z)",
    re.DOTALL,
)


def _with_disclaimer_footer(content: str) -> str:
    return content.rstrip() + "\n\n" + MEDICAL_DISCLAIMER_FOOTER + "\n"


def _append_before_disclaimer_footer(markdown: str, addition: str) -> str:
    footer_suffix = "\n\n" + MEDICAL_DISCLAIMER_FOOTER + "\n"
    body = markdown[:-len(footer_suffix)].rstrip() if markdown.endswith(footer_suffix) else markdown.rstrip()
    return _with_disclaimer_footer(body + "\n\n" + addition.strip())


class ReportService(
    DailyCardMixin,
    MetricsMixin,
    EventsMixin,
    ObservationsMixin,
    PatternsMixin,
    MonthlySectionsMixin,
    DoctorMixin,
):
    def __init__(
        self,
        *,
        cgm_repository: SQLiteCGMRepository,
        report_repository: SQLiteReportRepository,
        analytics_service: CGMAnalyticsService | None = None,
        event_detector: GlucoseEventDetector | None = None,
        safety_router: SafetyRouter | None = None,
        audit_logger: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.cgm_repository = cgm_repository
        self.report_repository = report_repository
        # Cadence-adaptive defaults (D053): generate() re-tunes non-injected
        # services to the observed sampling interval of the report window.
        self._services_injected = analytics_service is not None or event_detector is not None
        self.analytics_service = analytics_service or CGMAnalyticsService()
        self.event_detector = event_detector or GlucoseEventDetector()
        self.safety_router = safety_router or SafetyRouter(store=self.cgm_repository.store)
        # F3-B1: optional sink for citation-guard violations. The audit payload
        # carries counts only — never claim text, glucose values, or narrative.
        self.audit_logger = audit_logger
        from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
        self.memory_repository = SQLiteMemoryRepository(self.cgm_repository.store)

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
        if not self._services_injected and points:
            interval = median_interval_minutes([point.timestamp for point in points])
            self.analytics_service = CGMAnalyticsService(
                AnalyticsConfig(expected_interval_minutes=interval)
            )
            self.event_detector = GlucoseEventDetector(
                EventDetectionConfig(
                    expected_interval_minutes=interval,
                    timezone=report_input.timezone,
                )
            )
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
        safety_decision = self.safety_router.evaluate(
            scope=scope,
            points=points,
            now=report_input.anchor_at,
        )

        # Check vulnerable population
        vulnerable_items = self.memory_repository.list_profile_items(scope.user_id, key="vulnerable_population")
        is_vulnerable = False
        if vulnerable_items:
            val = vulnerable_items[0].value
            if isinstance(val, dict):
                is_vulnerable = bool(val.get("value") or val.get("vulnerable_population"))
            else:
                is_vulnerable = bool(val)

        # Check disclaimer acknowledgment
        ack_items = self.memory_repository.list_profile_items(scope.user_id, key="vulnerable_disclaimer_acknowledged")
        acknowledged = False
        if ack_items:
            val = ack_items[0].value
            if isinstance(val, dict):
                acknowledged = bool(val.get("value") or val.get("acknowledged"))
            else:
                acknowledged = bool(val)

        disclaimer_content = (
            "【安全免责声明】\n"
            "本报告包含基于您的血糖数据分析的建议。此分析仅供参考，不作为临床诊断或医疗决策依据。\n"
            "对于孕期、长辈等脆弱人群，请务必在医生指导下进行健康管理。\n"
            "若您已阅读并知晓上述内容，请输入“已知晓”以继续查看报告。"
        )

        is_disclaimer_mode = False

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
        elif is_vulnerable and not acknowledged:
            is_disclaimer_mode = True
            sections = [
                ReportSection(
                    section_id="safety_disclaimer",
                    kind="safety",
                    title="安全免责声明",
                    content=disclaimer_content,
                    data_scope=scope,
                    evidence_refs=[],
                    source_tracks=[ReportSourceTrack.FACT],
                    confidence=1.0,
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
                is_vulnerable=is_vulnerable,
            )
            # P1-5 (MVP audit): emotional-first as code. When the triggering
            # user message carries distress vocabulary, the report leads with
            # a deterministic empathy section — acknowledgement before any
            # number. Safety paths are unaffected (red zone / disclaimer
            # replace content wholesale above; yellow prefix stays first).
            if detect_affect(report_input.user_message):
                sections.insert(
                    0,
                    ReportSection(
                        section_id="affect_ack",
                        kind="companion",
                        title="先说一句",
                        content=(
                            "听起来你现在有点辛苦。数字可以先放一放，"
                            "下面的内容你想看再看，不想看也没关系。"
                        ),
                        data_scope=scope,
                        evidence_refs=[],
                        source_tracks=[ReportSourceTrack.FACT],
                        confidence=1.0,
                    ),
                )
            # 🟡 Yellow zone: prepend alert prefix to the first section
            if safety_decision.safety_result["status"] == "yellow_zone" and sections:
                alert_prefix = safety_decision.message or ""
                sections[0] = sections[0].model_copy(
                    update={"content": alert_prefix + "\n\n" + sections[0].content}
                )
        # F3-B3 (US3): carry the recovery double-check into the report so the
        # renderer can surface it in the header (analyze L1: only when present).
        safety_result_payload = dict(safety_decision.safety_result)
        if safety_decision.recovery_check is not None:
            safety_result_payload["recovery_check"] = safety_decision.recovery_check
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
            safety_result=safety_result_payload,
        )

        if safety_decision.safety_result["status"] == "red_zone":
            # L-12: strip RAG_MERGE_MARKER defensively (red_zone path normally
            # has no merge content, but guard against leakage).
            report.rendered_markdown = render_markdown(report).replace(RAG_MERGE_MARKER, "")
        elif is_disclaimer_mode:
            report.rendered_markdown = disclaimer_content
            report.route = "reports.generate.disclaimer"
            report.safety_result = {"status": "disclaimer_pending"}
        else:
            if report.audience == ReportAudience.CLINICIAN or report.report_type == ReportType.DOCTOR:
                report.rendered_markdown = self.render_clinical(report)
            else:
                report.rendered_markdown = self.render_companion(report)
            # F3-B1 (US1, C4): mandatory strict citation gate on the medical-claim
            # narrative before delivery. Runs only on the normal narrative path —
            # red-zone / disclaimer already replaced content wholesale (Principle
            # III), and the deterministic metric sections are never guarded
            # (analyze I2/I3/U1).
            self._apply_citation_gate(report_input, report, aggregate=aggregate)

        # RAG merge sentinels are renderer-internal only.  Strip them after
        # companion validation (which needs the sentinel) but before the report
        # is persisted or returned through a tool response.
        report.sections = [
            section.model_copy(
                update={"content": section.content.replace(RAG_MERGE_MARKER, "")}
            )
            for section in report.sections
        ]
        report.output_hash = _output_hash(report.rendered_markdown)
        return self.report_repository.create_report(report)

    def _apply_citation_gate(
        self,
        report_input: ReportInput,
        report: Report,
        aggregate: "GlucoseAggregate | None" = None,
    ) -> None:
        """Block delivery if the medical-claim narrative has an unbacked number.

        The guarded text is the externally-generated ``medical_narrative`` only;
        the backing set is the retrieved authoritative cards (regardless of
        ``verified`` this cycle — analyze I2). On failure the report is replaced
        with the persona "cannot confirm" response and a content-free violation
        is logged (analyze C1/C4).
        """
        narrative = (report_input.medical_narrative or "").strip()
        if not narrative:
            return
        documents = [
            {"text": doc.text, "title": doc.title, "source": doc.source, "citation": doc.citation}
            for doc in report_input.authoritative_context.documents
        ]
        # Correct positional order: documents FIRST, then generated_text (analyze
        # C1 — the prior draft swapped them, silently no-op'ing the guard).
        result = assert_authoritative_quotes(documents, narrative, strict=True)
        if result.ok:
            # H-09: for non-CLINICIAN audiences, also check companion text
            # compliance so blacklisted abbreviations in the medical narrative
            # don't bypass the companion guard.
            if report_input.audience != ReportAudience.CLINICIAN:
                from hermes_cgm_agent.services.reports.narrative_templates import (
                    check_companion_text,
                )
                violations = check_companion_text(narrative)
                if violations:
                    report.rendered_markdown = _with_disclaimer_footer(CITATION_BLOCK_TEMPLATE)
                    report.route = "reports.generate.companion_violation"
                    report.safety_result = {
                        "status": "companion_violation",
                        "violation_count": len(violations),
                    }
                    if self.audit_logger is not None:
                        self.audit_logger(
                            "companion_guard_blocked",
                            {"violations": violations},
                        )
                    return
            # Issue #8: attribution-consistency layer — the citation gate only
            # verifies NUMBERS and the companion guard only verifies TONE; a
            # causal attribution ("餐后小高峰") contradicting the deterministic
            # metrics passes both. Cross-check and append a correction note
            # (never rewrite — verbatim narrative is a citation-gate invariant).
            from hermes_cgm_agent.services.reports.attribution_guard import (
                ATTRIBUTION_CORRECTION_NOTE,
                attribution_consistency_check,
            )
            narrative_block = narrative
            attribution_violations = attribution_consistency_check(aggregate, narrative)
            if attribution_violations:
                narrative_block = f"{narrative}\n\n{ATTRIBUTION_CORRECTION_NOTE}"
                if self.audit_logger is not None:
                    self.audit_logger(
                        "attribution_inconsistency_flagged",
                        {
                            "report_id": report.report_id,
                            "user_id": report.user_id,
                            "violations": attribution_violations,
                        },
                    )
            report.rendered_markdown = _append_before_disclaimer_footer(
                report.rendered_markdown,
                "## 医学参考\n\n" + narrative_block,
            )
            return
        report.rendered_markdown = _with_disclaimer_footer(CITATION_BLOCK_TEMPLATE)
        report.route = "reports.generate.citation_blocked"
        report.safety_result = {
            "status": "citation_blocked",
            "violation_count": len(result.violations),
        }
        if self.audit_logger is not None:
            self.audit_logger(
                "citation_guard_blocked",
                {
                    "report_id": report.report_id,
                    "user_id": report.user_id,
                    "violation_count": len(result.violations),
                    "mode": result.mode,
                },
            )

    def render_clinical(self, report: Report) -> str:
        # E2: a doctor report built for a non-clinician audience still emits the
        # companion-style merge sentences, which carry the RAG_MERGE_MARKER
        # sentinel. The marker is a rendering-only signal, never reader-facing
        # content, so strip it from the clinical output too; the merge sentence
        # itself stays as user-facing context.
        return render_markdown(report).replace(RAG_MERGE_MARKER, "")

    def render_companion(self, report: Report) -> str:
        # F4 vs F3 tone isolation guard (F-8/N4): clinical abbreviations and
        # assertive phrases are a Principle IV HARD gate (raise); over-length is
        # tolerated (logged) so a long card never crashes report generation
        # (FR-013: narrative is a rendering concern, not a data concern).
        from hermes_cgm_agent.services.reports.narrative_templates import check_companion_text
        for section in report.sections:
            content_to_validate = section.content

            # Strip yellow zone warning prefix
            if content_to_validate.startswith("⚠️"):
                parts = content_to_validate.split("\n\n", 1)
                if len(parts) > 1:
                    content_to_validate = parts[1]

            # Strip RAG context merge messages which are standard additions.
            # E2: strip by MARKER (robust to wording changes) instead of
            # exact-string matching — observations.py prefixes each merge
            # sentence with RAG_MERGE_MARKER; this removes the marker + its
            # sentence so they don't count toward the tone/length guard.
            content_to_validate = _RAG_MERGE_SEGMENT_RE.sub("", content_to_validate)
            content_to_validate = content_to_validate.strip()

            max_len = 80
            if section.section_id == "daily_card":
                max_len = 50
            elif section.section_id == "patterns":
                max_len = 100
            violations = check_companion_text(content_to_validate, max_len=max_len)
            hard = [v for v in violations if v.startswith(("abbr:", "phrase:"))]
            if hard:
                raise ValueError(
                    f"Companion section '{section.section_id}' violates Principle IV: {hard}"
                )
            # length-only violations are tolerated here (rendering concern).
        # E2: remove the RAG_MERGE_MARKER sentinels from the final markdown;
        # the merge sentences themselves stay (user-facing context), only the
        # rendering marker must never reach the reader.
        return render_markdown(report).replace(RAG_MERGE_MARKER, "")

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
        is_vulnerable: bool = False,
    ) -> list[ReportSection]:
        audience = ReportAudience(report_input.audience)
        report_type = ReportType(report_input.report_type)
        timezone_name = report_input.timezone
        period_label = {
            ReportType.DAILY: "今天",
            ReportType.WEEKLY: "这一周",
            ReportType.MONTHLY: "这个月",
            ReportType.DOCTOR: "这两周",
        }.get(report_type, "这段时间")

        # Calculate escalation state
        if report_input.consecutive_anomaly_days is not None:
            consecutive_days = report_input.consecutive_anomaly_days
        else:
            from hermes_cgm_agent.services.scheduling.scheduler import (
                PushSchedulerConfig,
                PushSchedulerService,
            )
            from hermes_cgm_agent.services.audit import AuditService
            scheduler = PushSchedulerService(
                store=self.cgm_repository.store,
                config=PushSchedulerConfig(timezone=timezone_name),
                audit_service=AuditService(self.cgm_repository.store),  # L-25: inject for consistency
            )
            consecutive_days = scheduler.consecutive_anomaly_days(scope.user_id, scope.window_end)

        esc_state = EscalationState.derive(consecutive_days, is_vulnerable)

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
                    period_label=period_label,
                )
            ]

        # D056: every section is always BUILT (so the memory-candidate pipeline
        # and section-existence contracts stay intact); low-signal empty
        # sections mark themselves `omit_for_companion=True` and the renderer
        # hides them from everyday/family readers only. This keeps the report
        # short for a lay reader without touching data completeness or audit.
        sections = [
            self._daily_card_section(
                scope=scope,
                aggregate=aggregate,
                audience=audience,
                warnings=warnings,
                detected_events=detected_events,
                period_label=period_label,
            ),
            self._overview_section(scope, aggregate, warnings, audience, period_label=period_label),
            self._metrics_section(scope, aggregate, audience),
            self._data_quality_section(scope, warnings, audience),
            self._key_events_section(
                report_id,
                scope,
                events,
                audience,
                timezone_name=timezone_name,
            ),
            self._detected_events_section(scope, detected_events, audience),
            self._observations_section(
                scope,
                aggregate,
                report_input.memory_context,
                report_input.authoritative_context,
                audience,
            ),
            self._follow_up_section(scope, aggregate, events, audience, esc_state=esc_state, consecutive_days=consecutive_days, detected_events=detected_events, is_vulnerable=is_vulnerable),
        ]
        # US2/FR-004: state-aware L3 hypothesis narrative (companion audiences only;
        # clinician reports stay pure clinical, Principle IV / F3 isolation).
        if audience != ReportAudience.CLINICIAN:
            hyp_section = self._hypothesis_narrative_section(
                scope,
                audience,
                timezone_name=timezone_name,
            )
            if hyp_section is not None:
                sections.append(hyp_section)
        if report_type == ReportType.WEEKLY:
            sections.append(
                self._patterns_section(
                    report_id,
                    scope,
                    aggregate,
                    events,
                    detected_events,
                    audience,
                    timezone_name=timezone_name,
                )
            )
        if report_type == ReportType.MONTHLY:
            # G6: month-level overview (with MoM comparison) + monthly patterns.
            prev_aggregate = self._previous_month_aggregate(scope, aggregate)
            sections.insert(
                1,
                self._monthly_summary_section(scope, aggregate, prev_aggregate, audience),
            )
            sections.append(
                self._monthly_patterns_section(
                    scope,
                    aggregate,
                    detected_events,
                    audience,
                    timezone_name=timezone_name,
                )
            )
        if report_type == ReportType.DOCTOR:
            sections.append(
                self._doctor_appendix_section(scope, aggregate, events, detected_events, warnings, audience)
            )
        return sections

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
