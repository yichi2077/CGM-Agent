from __future__ import annotations

from hermes_cgm_agent.domain.report import DataQualitySeverity, Report, ReportAudience, ReportType


def render_markdown(report: Report) -> str:
    title = _report_title(report)
    lines = [
        f"# {title}",
        "",
        f"- 用户：`{report.user_id}`",
        f"- 时间范围：`{report.data_scope.window_start.isoformat()}` 至 `{report.data_scope.window_end.isoformat()}`",
        f"- 时区：`{report.timezone}`",
        f"- 叙事版本：`{_audience_label(report.audience)}`",
        "",
    ]
    if report.data_quality_warnings:
        lines.extend(["## 数据质量说明", ""])
        for warning in report.data_quality_warnings:
            lines.append(f"- [{_severity_label(warning.severity)}] {warning.message}")
        lines.append("")

    for section in report.sections:
        lines.extend([f"## {section.title}", "", section.content.strip(), ""])
        if section.warnings:
            lines.append("补充说明：")
            for warning in section.warnings:
                lines.append(f"- [{_severity_label(warning.severity)}] {warning.message}")
            lines.append("")
        if section.g8_memory_candidates:
            lines.append("待确认的记忆：")
            for candidate in section.g8_memory_candidates:
                lines.append(f"- {candidate.target_layer}: {candidate.summary}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


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


def _severity_label(severity: DataQualitySeverity) -> str:
    severity = DataQualitySeverity(severity)
    if severity == DataQualitySeverity.ERROR:
        return "重要"
    if severity == DataQualitySeverity.WARNING:
        return "提示"
    return "说明"
