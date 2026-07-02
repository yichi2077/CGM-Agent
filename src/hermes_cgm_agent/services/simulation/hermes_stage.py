from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HermesStageResult:
    status: str
    exit_code: int
    checks: dict[str, Any]
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "checks": self.checks,
            "message": self.message,
        }


class HermesStage:
    def __init__(self, *, db_path: Path, time_base: str, repo_path: Path | None = None) -> None:
        self.db_path = db_path
        self.time_base = time_base
        self.repo_path = repo_path or Path(
            os.getenv("CGM_HERMES_REPO", Path.home() / "AppData/Local/hermes/hermes-agent")
        )

    def preflight(self) -> HermesStageResult:
        checks: dict[str, Any] = {
            "repo_path": str(self.repo_path),
            "repo_exists": self.repo_path.exists(),
            "db_path": str(self.db_path),
            "db_exists": self.db_path.exists(),
            "time_base": self.time_base,
            "run_agent_importable": importlib.util.find_spec("hermes_cli") is not None,
        }
        errors: list[str] = []
        if not checks["repo_exists"]:
            errors.append("Hermes repo path is missing")
        if not checks["db_exists"]:
            errors.append("CGM_AGENT_DB_PATH does not exist")
        if self.time_base != "shift-to-now":
            errors.append("--hermes requires --time-base shift-to-now")
        if not checks["run_agent_importable"]:
            errors.append("hermes_cli is not importable in this Python environment")
        if errors:
            return HermesStageResult(
                status="preflight_failed",
                exit_code=2,
                checks=checks,
                message="; ".join(errors),
            )
        return HermesStageResult(status="ok", exit_code=0, checks=checks)

    def write_result(self, out_dir: Path, result: HermesStageResult) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "hermes_stage.json"
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path
