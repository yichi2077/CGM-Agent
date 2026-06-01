"""CGMMemoryProvider — self-built Hermes-compatible memory provider (D012/D018).

Implements the Hermes MemoryProvider contract (verified against local Hermes
0.15.1 `agent/memory_provider.py`: name / is_available / initialize /
get_tool_schemas + prefetch / sync_turn / queue_prefetch hooks) but lives in
THIS project as a service. It carries L1 + L3 (Hermes allows only one external
provider). It is NOT written into the Hermes install tree; a thin user-plugin
wrapper under `$HERMES_HOME/plugins/cgm_memory/` can adapt it later.

The contract is duck-typed here (no import of Hermes) so the project stays
decoupled from the Hermes SDK (D010). L2 maps to USER.md elsewhere; this
provider does not write USER.md directly.

- prefetch(query): recall L1 episodes + active L3 hypotheses (user_memory track).
- sync_turn(...): hook point for async consolidation (kept lightweight here).
- get_tool_schemas(): exposes memory.correct / memory.confirm style tools.
"""

from __future__ import annotations

from typing import Any

from hermes_cgm_agent.services.memory.assembler import MemoryContextAssembler
from hermes_cgm_agent.services.memory.consolidation import ConsolidationService
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class CGMMemoryProvider:
    """Hermes-compatible provider (duck-typed). Carries L1 + L3."""

    def __init__(self, store: SQLiteStore, *, user_id: str = "demo-user") -> None:
        self._store = store
        self._user_id = user_id
        self._repository = SQLiteMemoryRepository(store)
        self._assembler = MemoryContextAssembler(repository=self._repository)
        self._consolidation = ConsolidationService(repository=self._repository)
        self._session_id = ""

    @property
    def name(self) -> str:
        return "cgm_memory"

    def is_available(self) -> bool:
        # Local-only: ready as soon as the store exists. No network/credentials.
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        if kwargs.get("user_id"):
            self._user_id = str(kwargs["user_id"])

    def system_prompt_block(self) -> str:
        return (
            "CGM memory is active. Personal episodes and hypotheses are recalled "
            "as user_memory evidence and must be cited with uncertainty, never as "
            "authoritative medical fact."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        context = self._assembler.build_memory_context(
            user_id=self._user_id, query=query, top_k=5
        )
        if not context.items:
            return ""
        lines = ["[CGM user-memory recall]"]
        for item in context.items:
            lines.append(f"- ({item['layer']}) {item['summary']}")
        return "\n".join(lines)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        # Background warm-up is a no-op in the local service version.
        return None

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        # Consolidation is intentionally async/batch (run after reports/sessions),
        # not per-turn. Kept as a no-op hook to honor the contract.
        return None

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        # End-of-session is a natural consolidation trigger (D026).
        self._consolidation.consolidate(self._user_id)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "memory.confirm",
                "description": "Confirm or reject a pending CGM memory candidate.",
                "parameters": {
                    "type": "object",
                    "required": ["user_id", "candidate_id", "confirmed"],
                    "properties": {
                        "user_id": {"type": "string"},
                        "candidate_id": {"type": "string"},
                        "confirmed": {"type": "boolean"},
                    },
                },
            },
            {
                "name": "memory.correct",
                "description": "Apply an explicit user correction to L1/L2/L3 memory.",
                "parameters": {
                    "type": "object",
                    "required": ["user_id", "target", "correction"],
                    "properties": {
                        "user_id": {"type": "string"},
                        "target": {"type": "string", "enum": ["L1", "L2", "L3"]},
                        "correction": {"type": "object"},
                    },
                },
            },
        ]

    def shutdown(self) -> None:
        return None
