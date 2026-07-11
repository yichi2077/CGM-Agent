from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from hermes_cgm_agent.services.analytics import CGMAnalyticsService, GlucoseEventDetector
    from hermes_cgm_agent.services.data import SQLiteCGMRepository
    from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
    from hermes_cgm_agent.services.reports.repository import SQLiteReportRepository
    from hermes_cgm_agent.services.safety import SafetyRouter


class BaseSectionMixin:
    """Shared state declaration for section-builder mixins.

    Attributes are populated by ReportService.__init__; each mixin reads
    them through self. Annotation-only (no runtime assignment) so type
    checkers resolve cross-mixin self access through the common base.
    """

    cgm_repository: "SQLiteCGMRepository"
    report_repository: "SQLiteReportRepository"
    analytics_service: "CGMAnalyticsService"
    event_detector: "GlucoseEventDetector"
    safety_router: "SafetyRouter"
    audit_logger: "Callable[[str, dict[str, Any]], None] | None"
    memory_repository: "SQLiteMemoryRepository"
    _services_injected: bool
