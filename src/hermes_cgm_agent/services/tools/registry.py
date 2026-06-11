from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ToolStatus = Literal["planned", "active", "disabled"]
ToolRiskLevel = Literal["read", "write", "sensitive", "external"]


DATA_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["user_id", "window_start", "window_end"],
    "additionalProperties": False,
    "properties": {
        "user_id": {"type": "string"},
        "window_start": {"type": "string", "format": "date-time"},
        "window_end": {"type": "string", "format": "date-time"},
        "source": {"type": ["string", "null"]},
    },
}

EVIDENCE_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "ref_id"],
    "additionalProperties": False,
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "glucose_point",
                "aggregate",
                "event",
                "memory",
                "document",
                "user_memory",
                "authoritative_kb",
                "report",
            ],
        },
        "ref_id": {"type": "string"},
        "summary": {"type": ["string", "null"]},
    },
}


@dataclass(frozen=True)
class ToolSpec:
    """Declarative boundary for a CGM capability exposed to Hermes."""

    name: str
    group: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    owner_module: str
    status: ToolStatus = "planned"
    risk_level: ToolRiskLevel = "read"
    writes_audit: bool = True
    evidence_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "group": self.group,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "owner_module": self.owner_module,
            "status": self.status,
            "risk_level": self.risk_level,
            "writes_audit": self.writes_audit,
            "evidence_required": self.evidence_required,
        }


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def list(
        self,
        *,
        group: str | None = None,
        status: ToolStatus | None = None,
    ) -> list[ToolSpec]:
        specs = self._tools.values()
        if group is not None:
            specs = [spec for spec in specs if spec.group == group]
        if status is not None:
            specs = [spec for spec in specs if spec.status == status]
        return sorted(specs, key=lambda spec: spec.name)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {spec.name: spec.to_dict() for spec in self.list()}


def _object_schema(
    *,
    required: list[str],
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": required,
        "additionalProperties": False,
        "properties": properties,
    }


def _response_schema(properties: dict[str, Any]) -> dict[str, Any]:
    base_properties = {
        "status": {"type": "string", "enum": ["ok", "error"]},
        "evidence_refs": {"type": "array", "items": EVIDENCE_REF_SCHEMA},
        "audit_id": {"type": ["string", "null"]},
    }
    base_properties.update(properties)
    return _object_schema(
        required=["status", "evidence_refs", "audit_id"],
        properties=base_properties,
    )


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="timeseries.get_points",
            group="timeseries",
            owner_module="cgm_repository",
            description="Read normalized glucose points for a user and time window.",
            input_schema=_object_schema(
                required=["data_scope"],
                properties={
                    "data_scope": DATA_SCOPE_SCHEMA,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
            ),
            output_schema=_response_schema(
                {
                    "points": {
                        "type": "array",
                        "items": {"type": "object", "description": "A normalized glucose point."},
                    }
                }
            ),
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="timeseries.get_aggregate",
            group="timeseries",
            owner_module="analytics",
            description="Read or compute CGM aggregate metrics for a user and time window.",
            input_schema=_object_schema(
                required=["data_scope", "window_label"],
                properties={
                    "data_scope": DATA_SCOPE_SCHEMA,
                    "window_label": {"type": "string", "enum": ["day", "week", "14d", "month"]},
                },
            ),
            output_schema=_response_schema(
                {
                    "aggregate": {
                        "type": "object",
                        "description": "Aggregate CGM metrics (TIR/TAR/TBR/GMI/CV/MBG/LBGI/HBGI, coverage).",
                    }
                }
            ),
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="events.create",
            group="events",
            owner_module="event_repository",
            description="Create a user event or an unconfirmed candidate event on the CGM timeline.",
            input_schema=_object_schema(
                required=["user_id", "event"],
                properties={
                    "user_id": {"type": "string"},
                    "event": {
                        "type": "object",
                        "required": ["event_type", "ts_start"],
                        "additionalProperties": False,
                        "description": (
                            "The event to record. Only event_type and ts_start are required; "
                            "the system assigns the id and marks it as an unconfirmed, "
                            "agent-created candidate (the model cannot set id/created_by/user_confirmed)."
                        ),
                        "properties": {
                            "event_type": {
                                "type": "string",
                                "enum": [
                                    "meal",
                                    "exercise",
                                    "medication",
                                    "symptom",
                                    "note",
                                    "feedback",
                                    "clinic_followup",
                                ],
                                "description": "Type of event.",
                            },
                            "ts_start": {
                                "type": "string",
                                "format": "date-time",
                                "description": "Event start time (ISO 8601).",
                            },
                            "ts_end": {
                                "type": ["string", "null"],
                                "format": "date-time",
                                "description": "Optional event end time (ISO 8601).",
                            },
                            "payload": {
                                "type": "object",
                                "description": "Freeform details (e.g. meal description).",
                            },
                            "confidence": {
                                "type": ["number", "null"],
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                    },
                    "reason": {"type": ["string", "null"]},
                },
            ),
            output_schema=_response_schema({"event_id": {"type": "string"}}),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="events.confirm",
            group="events",
            owner_module="event_repository",
            description="Promote or reject an agent-created candidate event after user confirmation.",
            input_schema=_object_schema(
                required=["user_id", "event_id", "confirmed"],
                properties={
                    "user_id": {"type": "string"},
                    "event_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                    "correction": {"type": ["object", "null"]},
                },
            ),
            output_schema=_response_schema({"event_id": {"type": "string"}}),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="context.get_l0",
            group="context",
            owner_module="memory",
            description="Build the deterministic L0 working-memory context for a user.",
            input_schema=_object_schema(
                required=["user_id"],
                properties={
                    "user_id": {"type": "string"},
                    "anchor_at": {"type": ["string", "null"], "format": "date-time"},
                    "source": {"type": ["string", "null"]},
                },
            ),
            output_schema=_response_schema({"context": {"type": "object"}}),
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="reports.generate",
            group="reports",
            owner_module="reports",
            description="Generate a controlled CGM report from structured metrics, events, and evidence.",
            input_schema=_object_schema(
                required=["user_id", "report_type"],
                properties={
                    "user_id": {"type": "string"},
                    "data_scope": DATA_SCOPE_SCHEMA,
                    "report_type": {"type": "string", "enum": ["daily", "weekly", "doctor"]},
                    "audience": {"type": "string", "enum": ["self", "clinician", "family"]},
                    "language": {"type": "string", "enum": ["zh-CN", "en-US"]},
                    "timezone": {"type": "string"},
                    "report_anchor_time": {"type": "string"},
                    "anchor_at": {"type": "string", "format": "date-time"},
                    "memory_context": {"type": "object"},
                    "authoritative_context": {"type": "object"},
                    "include_candidate_events": {"type": "boolean"},
                    "retrieve_context": {"type": "boolean"},
                    "auto_ingest_memory": {"type": "boolean"},
                },
            ),
            output_schema=_response_schema(
                {
                    "report_id": {"type": "string"},
                    "sections": {"type": "array", "items": {"type": "object"}},
                    "rendered_markdown": {"type": "string"},
                    "g8_memory_candidates": {"type": "array", "items": {"type": "object"}},
                }
            ),
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="memory.list",
            group="memory",
            owner_module="memory",
            description="Browse stored CGM memory records by layer.",
            input_schema=_object_schema(
                required=["user_id", "layer"],
                properties={
                    "user_id": {"type": "string"},
                    "layer": {"type": "string", "enum": ["L1", "L2", "L3", "all", "candidates"]},
                    "limit": {"type": "integer", "minimum": 1},
                    "include_archived": {"type": "boolean"},
                    "candidate_status": {
                        "type": "string",
                        "enum": ["pending", "accepted", "rejected", "all"],
                    },
                },
            ),
            output_schema=_response_schema(
                {
                    "memories": {"type": "array", "items": {"type": "object"}},
                    "total_count": {"type": "integer"},
                    "candidates": {"type": "array", "items": {"type": "object"}},
                    "candidate_count": {"type": "integer"},
                }
            ),
            risk_level="read",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="memory.delete",
            group="memory",
            owner_module="memory",
            description="Delete a stored CGM memory record by layer and id.",
            input_schema=_object_schema(
                required=["user_id", "memory_id", "layer"],
                properties={
                    "user_id": {"type": "string"},
                    "memory_id": {"type": "string"},
                    "layer": {"type": "string", "enum": ["L1", "L2", "L3"]},
                },
            ),
            output_schema=_response_schema(
                {
                    "deleted_id": {"type": "string"},
                    "layer": {"type": "string"},
                }
            ),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="memory.confirm",
            group="memory",
            owner_module="memory",
            description="Confirm or reject a pending memory candidate; accepted candidates are promoted to memory.",
            input_schema=_object_schema(
                required=["user_id", "candidate_id", "confirmed"],
                properties={
                    "user_id": {"type": "string"},
                    "candidate_id": {"type": "string"},
                    "confirmed": {"type": "boolean"},
                },
            ),
            output_schema=_response_schema(
                {
                    "candidate_id": {"type": "string"},
                    # C8: align with executor payload (returns candidate_status,
                    # not status; top-level status is the ok/error envelope).
                    "candidate_status": {"type": "string"},
                }
            ),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="memory.correct",
            group="memory",
            owner_module="memory",
            description="Apply explicit user correction to profile, episodic memory, or hypotheses.",
            input_schema=_object_schema(
                required=["user_id", "target", "correction"],
                properties={
                    "user_id": {"type": "string"},
                    "target": {"type": "string", "enum": ["L1", "L2", "L3"]},
                    "correction": {"type": "object"},
                },
            ),
            output_schema=_response_schema({"memory_id": {"type": ["string", "null"]}}),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="hypothesis.update",
            group="memory",
            owner_module="hypothesis_engine",
            description="Update the state of a long-running CGM behavior hypothesis.",
            input_schema=_object_schema(
                required=["user_id", "hypothesis_id", "state"],
                properties={
                    "user_id": {"type": "string"},
                    "hypothesis_id": {"type": "string"},
                    "state": {
                        "type": "string",
                        "enum": ["candidate", "observing", "stable", "archived"],
                    },
                    "evidence_refs": {"type": "array", "items": EVIDENCE_REF_SCHEMA},
                },
            ),
            output_schema=_response_schema(
                {
                    "hypothesis_id": {"type": "string"},
                    "state": {"type": "string"},
                }
            ),
            risk_level="write",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="rag.authoritative_search",
            group="rag",
            owner_module="rag",
            description="Search the authoritative knowledge base, separate from personal memory.",
            input_schema=_object_schema(
                required=["query"],
                properties={
                    "query": {"type": "string", "minLength": 1},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "population": {
                        "type": ["string", "null"],
                        "description": (
                            "Free-text population; auto-normalized to a controlled "
                            "class (general/pediatric/pregnancy/elderly/inpatient). "
                            "general-baseline cards are always co-eligible."
                        ),
                    },
                },
            ),
            output_schema=_response_schema(
                {
                    "documents": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "kb_version": {"type": "string"},
                }
            ),
            risk_level="read",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="rag.verify_quotes",
            group="rag",
            owner_module="safety",
            description=(
                "Post-generation anti-hallucination gate (A2): verify that every "
                "significant medical number in GENERATED narrative text is backed "
                "by a retrieved authoritative card. Call this AFTER drafting any "
                "narrative that cites clinical numbers. Pass the retrieved "
                "`documents`, or a `query` to re-retrieve them. With strict=true, "
                "any unsupported number fails (ok=false)."
            ),
            input_schema=_object_schema(
                required=["generated_text"],
                properties={
                    "generated_text": {"type": "string", "minLength": 1},
                    "documents": {"type": "array", "items": {"type": "object"}},
                    "query": {"type": ["string", "null"]},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "strict": {"type": "boolean"},
                },
            ),
            output_schema=_response_schema(
                {
                    "ok": {"type": "boolean"},
                    "mode": {"type": "string"},
                    "violations": {"type": "array", "items": {"type": "string"}},
                    "checked_documents": {"type": "integer"},
                }
            ),
            risk_level="read",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="kb.approve",
            group="rag",
            owner_module="rag",
            description=(
                "Record clinical sign-off on a curated knowledge card: set "
                "verified=true with reviewer + reviewed_at provenance. Restricted "
                "to tier=curated cards; this is the ONLY sanctioned KB write path."
            ),
            input_schema=_object_schema(
                required=["card_id", "reviewer"],
                properties={
                    "card_id": {"type": "string", "minLength": 1},
                    "reviewer": {"type": "string", "minLength": 1},
                    "reviewed_at": {"type": ["string", "null"], "format": "date-time"},
                },
            ),
            output_schema=_response_schema(
                {
                    "approval_id": {"type": "string"},
                    "card_id": {"type": "string"},
                    "verified": {"type": "boolean"},
                    "reviewer": {"type": ["string", "null"]},
                    "reviewed_at": {"type": ["string", "null"]},
                    "tier": {"type": "string"},
                }
            ),
            risk_level="sensitive",
            evidence_required=False,
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="data.dexcom_sync",
            group="data",
            owner_module="dexcom_sync",
            description=(
                "Sync glucose readings (EGVs) and user events from the Dexcom "
                "cloud (API v3) into local CGM storage. Requires a prior "
                "dexcom-auth authorization for the user."
            ),
            input_schema=_object_schema(
                required=["user_id"],
                properties={
                    "user_id": {"type": "string"},
                    "days": {"type": "integer", "minimum": 1, "maximum": 90},
                    "force": {"type": "boolean"},
                },
            ),
            output_schema=_response_schema(
                {
                    "environment": {"type": "string"},
                    "window_start": {"type": ["string", "null"]},
                    "window_end": {"type": ["string", "null"]},
                    "egv_fetched": {"type": "integer"},
                    "egv_inserted": {"type": "integer"},
                    "egv_duplicate": {"type": "integer"},
                    "egv_skipped": {"type": "integer"},
                    "event_fetched": {"type": "integer"},
                    "event_inserted": {"type": "integer"},
                    "event_duplicate": {"type": "integer"},
                    "event_skipped": {"type": "integer"},
                }
            ),
            risk_level="external",
            evidence_required=False,
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="delivery.send",
            group="delivery",
            owner_module="delivery",
            description="Send an approved report or notification through an external channel.",
            input_schema=_object_schema(
                required=["user_id", "channel", "payload_ref"],
                properties={
                    "user_id": {"type": "string"},
                    "channel": {"type": "string", "enum": ["local_file", "email", "webhook"]},
                    "payload_ref": {"type": "string"},
                },
            ),
            output_schema=_response_schema(
                {
                    "delivery_id": {"type": "string"},
                    "delivery_status": {"type": "string", "enum": ["queued", "sent", "failed"]},
                    "manifest_path": {"type": ["string", "null"]},
                }
            ),
            risk_level="external",
            status="active",
        )
    )
    registry.register(
        ToolSpec(
            name="scheduling.push_tick",
            group="scheduling",
            owner_module="push_scheduler",
            description=(
                "Run a tiered-push scheduling tick for a user: evaluate which "
                "digest tiers (daily/weekly/monthly) are due, generate and record "
                "their pushes idempotently, and advance unobjected behavioral "
                "hypotheses via silent-consent. Hermes cron drives the cadence; "
                "the model only triggers the tick — it cannot control scheduling "
                "policy, tier selection, content, or silent-consent logic."
            ),
            input_schema=_object_schema(
                required=["user_id"],
                properties={
                    "user_id": {"type": "string"},
                    "now": {
                        "type": ["string", "null"],
                        "format": "date-time",
                        "description": (
                            "Optional ISO-8601 override for the current time "
                            "(testing/replay). Omit to use the wall clock."
                        ),
                    },
                },
            ),
            output_schema=_response_schema(
                {
                    "user_id": {"type": "string"},
                    "now": {"type": "string"},
                    "pushed": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": (
                            "Tiers pushed this tick "
                            "(tier/period_key/push_id/summary_id/content)."
                        ),
                    },
                    "silent_consent": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": (
                            "Hypotheses advanced candidate->observing "
                            "(hypothesis_id/statement/to)."
                        ),
                    },
                }
            ),
            risk_level="write",
            status="active",
        )
    )

    return registry
