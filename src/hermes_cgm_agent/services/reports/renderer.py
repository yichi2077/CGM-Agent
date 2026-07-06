from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain.report import DataQualitySeverity, Report, ReportAudience, ReportType

_ONE_SECOND = timedelta(seconds=1)

# F3-B1 (US1, analyze I1): the persona-aligned "cannot confirm" response the
# report pipeline returns when the strict citation guard blocks delivery of a
# medical-claim narrative whose numbers are not backed by an authoritative card.
# Distinct from the safety router's RED_ZONE_TEMPLATE (analyze I1, distinct
# names). Gentle, non-directive, offers a data-only alternative (Principle IV).
CITATION_BLOCK_TEMPLATE = (
    "这个问题涉及的医学数据我无法确认准确性。"
    "我可以帮你整理原始数据，复诊时带给医生。需要我生成数据摘要吗？"
)


def render_markdown(report: Report) -> str:
    title = _report_title(report)
    audience = ReportAudience(report.audience)
    report_type = ReportType(report.report_type)
    is_clinical = report_type == ReportType.DOCTOR or audience == ReportAudience.CLINICIAN
    if is_clinical:
        # Clinical/doctor report keeps the precise, auditable header (D056):
        # exact ISO window, user id, timezone — the medical-grade standard the
        # product is deliberately held to.
        lines = [
            f"# {title}",
            "",
            f"- 用户：`{report.user_id}`",
            f"- 时间范围：`{report.data_scope.window_start.isoformat()}` 至 `{report.data_scope.window_end.isoformat()}`",
            f"- 时区：`{report.timezone}`",
            f"- 叙事版本：`{_audience_label(report.audience)}`",
            "",
        ]
    else:
        # Everyday user / family: one friendly local date line — no ISO
        # timestamps, no UTC offset, no user id, no "叙事版本" jargon (D056).
        lines = [f"# {title}", "", f"_{_friendly_date_range(report)}_", ""]
    # F3-B3 (US3, analyze L1): surface the red-zone recovery double-check in the
    # header — both evaluations plus a recovery-confirmed indicator. Skipped
    # entirely when no recovery window is active.
    recovery = report.safety_result.get("recovery_check") if report.safety_result else None
    if recovery:
        confirmed = bool(recovery.get("recovery_confirmed"))
        original = recovery.get("original") or {}
        current = recovery.get("recovery") or {}
        lines.extend(
            [
                "## 恢复复核",
                "",
                f"- 此前红区评估：{_zone_label(original.get('status'))}",
                f"- 当前评估：{_zone_label(current.get('status'))}",
                f"- 恢复确认：{'是，已回到红区以外' if confirmed else '否，仍处于红区，请继续关注'}",
                "",
            ]
        )

    if report.data_quality_warnings:
        lines.extend(["## 数据质量说明", ""])
        for warning in report.data_quality_warnings:
            lines.append(f"- [{_severity_label(warning.severity)}] {warning.message}")
        lines.append("")

    for section in report.sections:
        # D056: hide low-signal empty sections from everyday/family readers
        # (they stay in report.sections for the candidate pipeline + audit).
        if section.omit_for_companion and not is_clinical:
            continue
        lines.extend([f"## {section.title}", "", section.content.strip(), ""])
        if section.warnings:
            lines.append("补充说明：")
            for warning in section.warnings:
                lines.append(f"- [{_severity_label(warning.severity)}] {warning.message}")
            lines.append("")
        # Memory candidates are an internal consolidation artifact. Surface the
        # raw "待确认的记忆 / L3:" list ONLY on the clinical report (D056); an
        # everyday user confirms memories through conversation, never by reading
        # an internal-layer tag in a report.
        if section.g8_memory_candidates and is_clinical:
            lines.append("待确认的记忆：")
            for candidate in section.g8_memory_candidates:
                lines.append(f"- {candidate.target_layer}: {candidate.summary}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def _friendly_date_range(report: Report) -> str:
    zone = ZoneInfo(report.timezone)
    start = report.data_scope.window_start.astimezone(zone)
    start_txt = f"{start.month}月{start.day}日"
    # A daily report covers one "glucose day" (07:00→07:00), which spans two
    # calendar dates; label it by the day it starts — a single, unambiguous
    # date reads far better for an everyday user than "6月4日 — 6月5日".
    if ReportType(report.report_type) == ReportType.DAILY:
        return start_txt
    # The window is left-closed, right-open with the end at the local anchor
    # time (e.g. 07:00 next day); the last *covered* day is the day before end.
    end_inclusive = report.data_scope.window_end.astimezone(zone) - _ONE_SECOND
    end_txt = f"{end_inclusive.month}月{end_inclusive.day}日"
    if start_txt == end_txt:
        return start_txt
    return f"{start_txt} — {end_txt}"


def _report_title(report: Report) -> str:
    report_type = ReportType(report.report_type)
    audience = ReportAudience(report.audience)
    if report_type == ReportType.DOCTOR or audience == ReportAudience.CLINICIAN:
        return "医生报告"
    if report_type == ReportType.WEEKLY:
        return "血糖周报"
    return "血糖日报"


def _audience_label(audience: ReportAudience) -> str:
    audience = ReportAudience(audience)
    if audience == ReportAudience.CLINICIAN:
        return "医生版"
    if audience == ReportAudience.FAMILY:
        return "家属版"
    return "用户版"


def _zone_label(status: object) -> str:
    return {
        "red_zone": "红区",
        "yellow_zone": "黄区",
        "clear": "安全范围",
    }.get(str(status), str(status))


def _severity_label(severity: DataQualitySeverity) -> str:
    severity = DataQualitySeverity(severity)
    if severity == DataQualitySeverity.ERROR:
        return "重要"
    if severity == DataQualitySeverity.WARNING:
        return "提示"
    return "说明"
