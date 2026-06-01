from __future__ import annotations

from hermes_cgm_agent.domain.report import Report


def render_markdown(report: Report) -> str:
    lines = [
        f"# CGM {report.report_type} report",
        "",
        f"- User: `{report.user_id}`",
        f"- Window: `{report.data_scope.window_start.isoformat()}` to `{report.data_scope.window_end.isoformat()}`",
        f"- Timezone: `{report.timezone}`",
        "",
    ]
    if report.data_quality_warnings:
        lines.extend(["## Data Quality Warnings", ""])
        for warning in report.data_quality_warnings:
            lines.append(f"- [{warning.severity}] {warning.message}")
        lines.append("")

    for section in report.sections:
        lines.extend([f"## {section.title}", "", section.content.strip(), ""])
        if section.warnings:
            lines.append("Warnings:")
            for warning in section.warnings:
                lines.append(f"- [{warning.severity}] {warning.message}")
            lines.append("")
        if section.g8_memory_candidates:
            lines.append("Memory candidates:")
            for candidate in section.g8_memory_candidates:
                lines.append(f"- {candidate.target_layer}: {candidate.summary}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"
