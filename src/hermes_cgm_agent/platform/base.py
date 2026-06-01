from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatRequest:
    prompt: str
    model: str | None = None
    provider: str | None = None
    toolsets: str | None = None
    skills: str | None = None
    resume: str | None = None
    continue_session: str | None = None
    max_turns: int | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ChatResult:
    text: str
    raw_stdout: str
    raw_stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class PlatformStatus:
    available: bool
    name: str
    version: str | None = None
    executable: str | None = None
    detail: str | None = None


class AgentPlatform(Protocol):
    def status(self) -> PlatformStatus:
        """Return platform availability and version information."""

    def chat(self, request: ChatRequest) -> ChatResult:
        """Run an open-ended chat request through the backing agent platform."""

