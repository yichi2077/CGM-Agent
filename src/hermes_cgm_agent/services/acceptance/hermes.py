from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HermesResponse:
    response: str
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    error: str | None = None


class HermesClient:
    def __init__(
        self,
        *,
        executable: Path,
        hermes_home: Path,
        db_path: Path,
        project_root: Path,
        user_id: str,
        provider: str,
        model: str,
        anchor_at: str | None = None,
        data_source: str | None = None,
        timezone_name: str = "UTC",
        timeout_seconds: int = 180,
    ) -> None:
        self.executable = executable
        self.hermes_home = hermes_home
        self.db_path = db_path
        self.project_root = project_root
        self.user_id = user_id
        self.provider = provider
        self.model = model
        self.anchor_at = anchor_at
        self.data_source = data_source
        self.timezone_name = timezone_name
        self.timeout_seconds = timeout_seconds

    def environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HERMES_HOME": str(self.hermes_home),
                "CGM_AGENT_DB_PATH": str(self.db_path),
                "CGM_AGENT_USER_ID": self.user_id,
                "CGM_AGENT_ENFORCE_USER_ID": "1",
                "CGM_AGENT_PROJECT_ROOT": str(self.project_root),
                "PYTHONPATH": os.pathsep.join(
                    [str(self.project_root / "src"), env.get("PYTHONPATH", "")]
                ).strip(os.pathsep),
            }
        )
        if self.anchor_at:
            env["CGM_AGENT_ACCEPTANCE_ANCHOR_AT"] = self.anchor_at
            env["CGM_AGENT_ENFORCE_TIME_ANCHOR"] = "1"
            env["CGM_AGENT_ACCEPTANCE_TIMEZONE"] = self.timezone_name
        if self.data_source:
            env["CGM_AGENT_ACCEPTANCE_SOURCE"] = self.data_source
            env["CGM_AGENT_ENFORCE_DATA_SOURCE"] = "1"
        return env

    def configure_memory_provider(self) -> HermesResponse:
        return self._run(["memory", "setup", "cgm_memory"], timeout=60)

    def memory_status(self) -> HermesResponse:
        return self._run(["memory", "status"], timeout=60)

    def smoke(self) -> HermesResponse:
        return self.run("只回复：CGM验收连接正常。", scenario_id="provider-smoke")

    def run(self, prompt: str, *, scenario_id: str) -> HermesResponse:
        return self._run(
            [
                "chat",
                "-q",
                prompt,
                "-Q",
                "--max-turns",
                "6",
                "--toolsets",
                "cgm",
                "--pass-session-id",
                "--source",
                f"hermes-accept:{scenario_id}",
                "--provider",
                self.provider,
                "--model",
                self.model,
            ],
            timeout=self.timeout_seconds,
        )

    def _run(self, args: list[str], *, timeout: int) -> HermesResponse:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [str(self.executable), *args],
                env=self.environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            return HermesResponse(
                response=_extract_response(stdout),
                exit_code=completed.returncode,
                duration_seconds=time.monotonic() - started,
                stdout=stdout,
                stderr=stderr,
                error=None if completed.returncode == 0 else _safe_error(stdout, stderr),
            )
        except subprocess.TimeoutExpired as exc:
            return HermesResponse(
                response="",
                exit_code=124,
                duration_seconds=time.monotonic() - started,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                error="Hermes chat timed out",
            )
        except OSError as exc:
            return HermesResponse(
                response="",
                exit_code=127,
                duration_seconds=time.monotonic() - started,
                stdout="",
                stderr="",
                error=f"Hermes executable failed: {type(exc).__name__}",
            )


def default_hermes_executable() -> Path:
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
        if candidate.exists():
            return candidate
    return Path(os.getenv("HERMES_BIN", "hermes"))


def normalize_provider(provider: str) -> str:
    """Return the provider spelling accepted by Hermes' CLI resolver.

    Hermes normalizes custom provider names to ``custom:<lowercase-slug>``.
    Keeping this in the acceptance layer makes a copied validation profile
    deterministic and avoids passing display names (or secrets) to a child
    process.
    """

    value = provider.strip()
    if not value:
        return value
    if value.lower().startswith("custom:"):
        value = value.split(":", 1)[1]
        return "custom:" + value.strip().lower().replace(" ", "-")
    return value


def _extract_response(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    # Hermes quiet mode emits the final answer plus a session footer. Remove
    # only obvious protocol lines; preserve the model's text verbatim.
    kept = [line for line in lines if not line.startswith(("Session:", "session_id:", "╭", "╰"))]
    return "\n".join(kept).strip()


def _safe_error(stdout: str, stderr: str) -> str:
    text = f"{stdout}\n{stderr}".strip()
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    return text[-1000:] or "Hermes returned a non-zero exit code"
