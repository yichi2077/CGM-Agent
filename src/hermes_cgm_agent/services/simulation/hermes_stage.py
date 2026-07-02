from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _default_hermes_repo() -> Path:
    """Resolve the Hermes repo path the same way test_hermes_e2e.py does.

    Prefer ``$CGM_HERMES_REPO``, then ``$LOCALAPPDATA/hermes/hermes-agent``
    (Windows), falling back to ``~/.hermes/hermes-agent`` for POSIX installs.
    """
    override = os.getenv("CGM_HERMES_REPO")
    if override:
        return Path(override)
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes" / "hermes-agent"
    return Path.home() / ".hermes" / "hermes-agent"


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
        self.repo_path = repo_path or _default_hermes_repo()

    def _run_agent_importable(self) -> bool:
        """Probe whether Hermes's ``run_agent`` module resolves.

        Mirrors test_hermes_e2e.py: the Hermes repo must be on ``sys.path``
        before ``run_agent`` can be found, so insert it first when present.
        """
        if not self.repo_path.exists():
            return False
        repo_str = str(self.repo_path)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        return importlib.util.find_spec("run_agent") is not None

    def preflight(self) -> HermesStageResult:
        checks: dict[str, Any] = {
            "repo_path": str(self.repo_path),
            "repo_exists": self.repo_path.exists(),
            "db_path": str(self.db_path),
            "db_exists": self.db_path.exists(),
            "time_base": self.time_base,
            "run_agent_importable": self._run_agent_importable(),
        }
        errors: list[str] = []
        if not checks["repo_exists"]:
            errors.append("Hermes repo path is missing")
        if not checks["db_exists"]:
            errors.append("CGM_AGENT_DB_PATH does not exist")
        if self.time_base != "shift-to-now":
            errors.append("--hermes requires --time-base shift-to-now")
        if not checks["run_agent_importable"]:
            errors.append("run_agent is not importable in this Python environment")
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
