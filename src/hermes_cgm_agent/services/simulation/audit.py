from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SimulationIssue:
    stage: str
    sim_now: str | None
    reading_index: int | None
    message: str
    traceback: str | None = None


@dataclass
class SimulationAudit:
    run_id: str
    out_dir: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: list[SimulationIssue] = field(default_factory=list)
    invariants: dict[str, Any] = field(default_factory=dict)
    acceptance_checks: dict[str, bool] = field(default_factory=dict)
    acceptance_comparisons: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: list[dict[str, str]] = field(default_factory=list)

    def record(self, stage: str, **payload: Any) -> None:
        self.records.append(
            {"sequence": len(self.records) + 1, "run_id": self.run_id, "stage": stage, **payload}
        )

    def link(
        self,
        *,
        from_stage: str,
        from_id: str,
        to_stage: str,
        to_id: str,
        relation: str,
    ) -> None:
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

    def issue(
        self,
        stage: str,
        *,
        sim_now: datetime | None = None,
        reading_index: int | None = None,
        message: str,
        traceback: str | None = None,
    ) -> None:
        self.issues.append(
            SimulationIssue(
                stage=stage,
                sim_now=sim_now.isoformat() if sim_now else None,
                reading_index=reading_index,
                message=message,
                traceback=traceback,
            )
        )

    def set_invariant(self, key: str, value: Any) -> None:
        self.invariants[key] = value

    def require(
        self,
        key: str,
        passed: bool,
        *,
        message: str,
        expected: Any = True,
        actual: Any = None,
    ) -> None:
        """Record a machine-readable acceptance check and fail loudly."""
        self.acceptance_checks[key] = bool(passed)
        self.acceptance_comparisons[key] = {
            "passed": bool(passed),
            "expected": expected,
            "actual": actual if actual is not None else bool(passed),
        }
        if not passed:
            self.issue(stage="acceptance", message=message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "records": self.records,
            "timeline": self.records,
            "links": self.links,
            "issues": [asdict(issue) for issue in self.issues],
            "invariants": self.invariants,
            "acceptance": {
                "passed": all(self.acceptance_checks.values()),
                "checks": self.acceptance_checks,
                "comparisons": self.acceptance_comparisons,
            },
            "status": "ok" if not self.issues else "failed",
        }

    def write(self) -> tuple[Path, Path]:
        # L-21: Simulation audit reports may contain PHI (glucose values,
        # event descriptions). Store them in an encrypted/protected directory
        # or encrypt manually before distribution. The JSON payload is the
        # authoritative record; the MD is for human review only.
        import logging
        logging.getLogger("hermes_cgm_agent.simulation").warning(
            "Writing simulation audit report to %s — ensure this directory "
            "is on an encrypted/protected filesystem (may contain PHI).",
            self.out_dir,
        )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        json_path = self.out_dir / "simulation_report.json"
        md_path = self.out_dir / "simulation_report.md"
        payload = self.to_dict()
        json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        md_path.write_text(self._markdown(payload), encoding="utf-8")
        return json_path, md_path

    def _markdown(self, payload: dict[str, Any]) -> str:
        lines = [
            f"# Simulation Report {self.run_id}",
            "",
            f"- Status: {payload['status']}",
            f"- Records: {len(self.records)}",
            f"- Issues: {len(self.issues)}",
            "",
            "## Invariants",
        ]
        for key, value in sorted(self.invariants.items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.extend(["", "## Acceptance checks"])
        for key, value in sorted(self.acceptance_checks.items()):
            lines.append(f"- [{'x' if value else ' '}] `{key}`")
        lines.extend(["", "## Stage links"])
        for link in self.links:
            lines.append(
                f"- `{link['from_stage']}:{link['from_id']}` "
                f"--{link['relation']}--> `{link['to_stage']}:{link['to_id']}`"
            )
        if self.issues:
            lines.extend(["", "## Issues"])
            for issue in self.issues:
                lines.append(
                    f"- {issue.stage} at {issue.sim_now or 'n/a'} "
                    f"index={issue.reading_index}: {issue.message}"
                )
        return "\n".join(lines) + "\n"
