from __future__ import annotations

from hermes_cgm_agent.platform.base import ChatRequest, ChatResult, PlatformStatus


class LocalAgentPlatform:
    """Small in-process test double for platform-facing code.

    Production open-ended chat should use HermesCliPlatform.
    """

    def status(self) -> PlatformStatus:
        return PlatformStatus(
            available=True,
            name="local-test-platform",
            version="0.1.0",
            executable=None,
            detail="Local test double. Not used for production chat.",
        )

    def chat(self, request: ChatRequest) -> ChatResult:
        text = f"[local-test-platform] {request.prompt}"
        return ChatResult(text=text, raw_stdout=text, raw_stderr="", returncode=0)

