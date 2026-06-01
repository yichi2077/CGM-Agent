from __future__ import annotations

from hermes_cgm_agent.platform.base import AgentPlatform, ChatRequest, ChatResult
from hermes_cgm_agent.platform.hermes_cli import HermesCliPlatform


class AgentChatService:
    """Open-ended chat service backed by Hermes."""

    def __init__(self, platform: AgentPlatform | None = None) -> None:
        self.platform = platform or HermesCliPlatform()

    def ask(self, prompt: str, **kwargs: object) -> ChatResult:
        return self.platform.chat(ChatRequest(prompt=prompt, **kwargs))

