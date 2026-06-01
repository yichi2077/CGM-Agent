from __future__ import annotations

import json
from typing import Any

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.domain.report import Report
from hermes_cgm_agent.storage.sqlite import SQLiteStore, utc_now


class SQLiteReportRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def create_report(self, report: Report) -> Report:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO reports (
                    report_id, user_id, report_type, audience, window_start,
                    window_end, timezone, report_anchor_time, status,
                    sections_json, rendered_markdown, rendered_path,
                    evidence_refs_json, data_quality_warnings_json,
                    g8_memory_candidates_json, source_versions_json,
                    template_version, output_hash, route, safety_result_json,
                    audit_id, generated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.user_id,
                    report.report_type,
                    report.audience,
                    report.data_scope.window_start.isoformat(),
                    report.data_scope.window_end.isoformat(),
                    report.timezone,
                    report.report_anchor_time.isoformat(timespec="minutes"),
                    report.status,
                    _json([section.model_dump(mode="json") for section in report.sections]),
                    report.rendered_markdown,
                    report.rendered_path,
                    _json([ref.model_dump(mode="json") for ref in report.evidence_refs]),
                    _json([warning.model_dump(mode="json") for warning in report.data_quality_warnings]),
                    _json([candidate.model_dump(mode="json") for candidate in report.g8_memory_candidates]),
                    _json(report.source_versions),
                    report.template_version,
                    report.output_hash,
                    report.route,
                    _json(report.safety_result),
                    report.audit_id,
                    report.generated_at.isoformat(),
                    utc_now(),
                ),
            )
        return self.get_report(report.report_id)

    def get_report(self, report_id: str) -> Report:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM reports
                WHERE report_id = ?
                """,
                (report_id,),
            ).fetchone()
        if row is None:
            raise KeyError(report_id)
        return self._row_to_report(row)

    def update_audit_id(self, report_id: str, audit_id: str) -> Report:
        with self.store.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE reports
                SET audit_id = ?
                WHERE report_id = ?
                """,
                (audit_id, report_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(report_id)
        return self.get_report(report_id)

    def list_reports(
        self,
        *,
        user_id: str,
        data_scope: DataScope | None = None,
        report_type: str | None = None,
        limit: int = 50,
    ) -> list[Report]:
        values: list[Any] = [user_id]
        filters = ["user_id = ?"]
        if data_scope is not None:
            filters.append("window_start >= ?")
            filters.append("window_end <= ?")
            values.extend([data_scope.window_start.isoformat(), data_scope.window_end.isoformat()])
        if report_type is not None:
            filters.append("report_type = ?")
            values.append(report_type)
        values.append(limit)
        with self.store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM reports
                WHERE {' AND '.join(filters)}
                ORDER BY created_at DESC, report_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_to_report(row) for row in rows]

    @staticmethod
    def _row_to_report(row: Any) -> Report:
        return Report(
            report_id=row["report_id"],
            user_id=row["user_id"],
            report_type=row["report_type"],
            audience=row["audience"],
            data_scope={
                "user_id": row["user_id"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
            },
            timezone=row["timezone"],
            report_anchor_time=row["report_anchor_time"],
            status=row["status"],
            sections=json.loads(row["sections_json"] or "[]"),
            rendered_markdown=row["rendered_markdown"],
            rendered_path=row["rendered_path"],
            evidence_refs=json.loads(row["evidence_refs_json"] or "[]"),
            data_quality_warnings=json.loads(row["data_quality_warnings_json"] or "[]"),
            g8_memory_candidates=json.loads(row["g8_memory_candidates_json"] or "[]"),
            source_versions=json.loads(row["source_versions_json"] or "{}"),
            template_version=row["template_version"],
            output_hash=row["output_hash"],
            route=row["route"],
            safety_result=json.loads(row["safety_result_json"] or "{}"),
            audit_id=row["audit_id"],
            generated_at=row["generated_at"],
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)
