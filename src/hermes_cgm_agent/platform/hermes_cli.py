from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from hermes_cgm_agent.config import DEFAULT_HERMES_EXE, AppConfig
from hermes_cgm_agent.platform.base import ChatRequest, ChatResult, PlatformStatus


class HermesCliPlatform:
    """Hermes-backed platform using the installed `hermes` CLI.

    This adapter intentionally delegates open-ended conversation to Hermes.
    Project-specific CGM services should call Hermes through this boundary,
    not implement a separate general chat engine.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig.from_env()
        self.hermes_bin = self._resolve_hermes_bin(self.config.hermes_bin)

    @staticmethod
    def _resolve_hermes_bin(configured: str | None) -> str:
        if configured:
            return configured
        discovered = shutil.which("hermes")
        if discovered:
            return discovered
        if DEFAULT_HERMES_EXE.exists():
            return str(DEFAULT_HERMES_EXE)
        return "hermes"

    def status(self) -> PlatformStatus:
        try:
            completed = subprocess.run(
                [self.hermes_bin, "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return PlatformStatus(
                available=False,
                name="hermes",
                executable=self.hermes_bin,
                detail=str(exc),
            )

        output = (completed.stdout or completed.stderr).strip()
        return PlatformStatus(
            available=completed.returncode == 0,
            name="hermes",
            version=output.splitlines()[0] if output else None,
            executable=self.hermes_bin,
            detail=output or None,
        )

    def chat(self, request: ChatRequest) -> ChatResult:
        command = self._build_chat_command(request)
        timeout = request.timeout_seconds or self.config.timeout_seconds
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ChatResult(
                text="",
                raw_stdout="",
                raw_stderr=str(exc),
                returncode=1,
            )
        text = completed.stdout.strip()
        return ChatResult(
            text=text,
            raw_stdout=completed.stdout,
            raw_stderr=completed.stderr,
            returncode=completed.returncode,
        )

    def _build_chat_command(self, request: ChatRequest) -> list[str]:
        command = [
            self.hermes_bin,
            "chat",
            "--query",
            request.prompt,
            "--quiet",
            "--source",
            "tool",
        ]

        model = request.model or self.config.default_model
        provider = request.provider or self.config.default_provider
        toolsets = request.toolsets or self.config.default_toolsets
        skills = request.skills or self.config.default_skills

        if model:
            command.extend(["--model", model])
        if provider:
            command.extend(["--provider", provider])
        if toolsets:
            command.extend(["--toolsets", toolsets])
        if skills:
            command.extend(["--skills", skills])
        if request.resume:
            command.extend(["--resume", request.resume])
        if request.continue_session is not None:
            command.append("--continue")
            if request.continue_session:
                command.append(request.continue_session)
        if request.max_turns is not None:
            command.extend(["--max-turns", str(request.max_turns)])

        return command

    @property
    def install_root(self) -> Path | None:
        exe = Path(self.hermes_bin)
        try:
            return exe.parents[2] if exe.name.lower().startswith("hermes") else None
        except IndexError:
            return None
