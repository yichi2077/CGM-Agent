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

    def record(self, stage: str, **payload: Any) -> None:
        self.records.append({"stage": stage, **payload})

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "records": self.records,
            "issues": [asdict(issue) for issue in self.issues],
            "invariants": self.invariants,
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
        if self.issues:
            lines.extend(["", "## Issues"])
            for issue in self.issues:
                lines.append(
                    f"- {issue.stage} at {issue.sim_now or 'n/a'} "
                    f"index={issue.reading_index}: {issue.message}"
                )
        return "\n".join(lines) + "\n"
