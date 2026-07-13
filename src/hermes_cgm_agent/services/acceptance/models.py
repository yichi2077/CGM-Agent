from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AcceptanceConfig:
    source_db: str
    user_id: str
    duration_hours: int = 72
    output_dir: str | None = None
    timezone_name: str = "Asia/Shanghai"
    hermes_home: str | None = None
    hermes_bin: str | None = None
    provider: str | None = None
    provider_user_agent: str | None = None
    provider_max_tokens: int | None = None
    model: str = "gpt-5.5"
    deliver: str | None = None
    max_model_calls: int = 30
    max_external_messages: int = 6
    activate_on_pass: bool = False
    run_model: bool = True
    send_external: bool = False
    timeout_seconds: int = 180


@dataclass
class AcceptanceLedger:
    run_id: str
    records: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)

    def record(self, stage: str, **payload: Any) -> int:
        sequence = len(self.records) + 1
        self.records.append(
            {
                "sequence": sequence,
                "run_id": self.run_id,
                "stage": stage,
                "created_at": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
                **payload,
            }
        )
        return sequence

    def link(self, from_stage: str, from_id: str, to_stage: str, to_id: str, relation: str) -> None:
        self.links.append(
            {
                "run_id": self.run_id,
                "from_stage": from_stage,
                "from_id": from_id,
                "to_stage": to_stage,
                "to_id": to_id,
                "relation": relation,
            }
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    category: str
    prompt: str
    expected_terms: tuple[str, ...] = ()
    expected_tool_fragments: tuple[str, ...] = ()
    rag_query: str | None = None
    max_chars: int = 600
    proactive: bool = False


@dataclass
class ScenarioResult:
    scenario: Scenario
    status: str
    response: str
    exit_code: int
    duration_seconds: float
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    oracle: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self, *, redact_response: bool = False) -> dict[str, Any]:
        data = {
            "scenario_id": self.scenario.scenario_id,
            "category": self.scenario.category,
            "prompt": self.scenario.prompt,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "tool_calls": self.tool_calls,
            "oracle": self.oracle,
            "checks": self.checks,
            "error": self.error,
        }
        if redact_response:
            # Public CI artifacts must not expose the model answer, raw
            # personal metrics, user id, Hermes session/audit ids, or delivery
            # payloads. Keep only deterministic shape/topic information that
            # lets a reviewer understand which gate ran.
            data["response"] = "[REDACTED]"
            data["tool_calls"] = [
                {
                    "tool_name": call.get("tool_name"),
                    "status": call.get("status"),
                    "doc_ids": list(call.get("doc_ids") or []),
                }
                for call in self.tool_calls
            ]
            data["oracle"] = {
                "point_count": self.oracle.get("point_count"),
                "event_count": self.oracle.get("event_count"),
                "event_types": self.oracle.get("event_types", {}),
                "rag_doc_ids": self.oracle.get("rag_doc_ids", []),
            }
        else:
            data["response"] = self.response
        return data


@dataclass(frozen=True)
class AcceptanceResult:
    status: str
    exit_code: int
    run_id: str
    output_dir: Any
    report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "run_id": self.run_id,
            "output_dir": str(self.output_dir),
            "report": self.report,
        }
