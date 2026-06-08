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
- get_tool_schemas(): exposes memory.correct / memory.confirm / list / delete tools.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Protocol

from hermes_cgm_agent.domain import EvidenceRef, MemoryCandidate, MemoryLayer
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.assembler import MemoryContextAssembler
from hermes_cgm_agent.services.memory.consolidation import ConsolidationService
from hermes_cgm_agent.services.memory.l0_builder import L0ContextBuilder
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository, new_id
from hermes_cgm_agent.services.memory.user_md_sync import UserMDSyncService
from hermes_cgm_agent.storage.sqlite import SQLiteStore


# Single source of truth for the memory tool schemas exposed to Hermes. The
# Hermes-facing wrapper (`integrations/hermes/cgm_memory`) imports this so it can
# answer `get_tool_schemas()` before `initialize()` without a divergent
# hardcoded copy (NEW-5).
MEMORY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "memory.list",
        "description": "Browse CGM memory records and pending review candidates by layer.",
        "parameters": {
            "type": "object",
            "required": ["user_id", "layer"],
            "properties": {
                "user_id": {"type": "string"},
                "layer": {"type": "string", "enum": ["L1", "L2", "L3", "all", "candidates"]},
                "limit": {"type": "integer", "minimum": 1},
                "include_archived": {"type": "boolean"},
                "candidate_status": {
                    "type": "string",
                    "enum": ["pending", "accepted", "rejected", "all"],
                },
            },
        },
    },
    {
        "name": "memory.delete",
        "description": "Delete a CGM memory record by layer and id.",
        "parameters": {
            "type": "object",
            "required": ["user_id", "memory_id", "layer"],
            "properties": {
                "user_id": {"type": "string"},
                "memory_id": {"type": "string"},
                "layer": {"type": "string", "enum": ["L1", "L2", "L3"]},
            },
        },
    },
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


class CGMMemoryProvider:
    """Hermes-compatible provider (duck-typed). Carries L1 + L3."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        user_id: str = "demo-user",
        extractor: "ConversationMemoryExtractor | None" = None,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._repository = SQLiteMemoryRepository(store)
        self._assembler = MemoryContextAssembler(repository=self._repository)
        self._consolidation = ConsolidationService(
            repository=self._repository,
            audit_service=AuditService(store),
        )
        self._extractor = extractor
        self._session_id = ""
        self._hermes_home = ""
        self._platform = ""
        self._agent_context = "primary"
        self._session_turns: dict[str, list[str]] = {}

    @property
    def name(self) -> str:
        return "cgm_memory"

    def is_available(self) -> bool:
        # Local-only: ready as soon as the store exists. No network/credentials.
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._platform = str(kwargs.get("platform") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        if kwargs.get("user_id"):
            self._user_id = str(kwargs["user_id"])
        self._session_turns.setdefault(session_id, [])

    def system_prompt_block(self) -> str:
        block = (
            "CGM memory is active. Personal episodes and hypotheses are recalled "
            "as user_memory evidence and must be cited with uncertainty, never as "
            "authoritative medical fact."
        )
        if self._hermes_home:
            block += f" Runtime scope: {self._hermes_home}."
        return block

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        lines: list[str] = []
        # Warm state summary ("dreaming", D034) injected first as background.
        latest = self._repository.latest_summary(self._user_id)
        if latest is not None:
            lines.append(f"[CGM state summary] {latest.content}")
        try:
            l0 = L0ContextBuilder(
                repository=SQLiteCGMRepository(self._store),
            ).build(user_id=self._user_id)
            if l0.window_summary.point_count or l0.key_glucose_events:
                lines.append(
                    "[CGM L0 context] "
                    f"{l0.window.span_days}d points={l0.window_summary.point_count}, "
                    f"recent_points={len(l0.high_res_recent)}, "
                    f"hourly={len(l0.mid_far_hourly)}, "
                    f"events={len(l0.key_glucose_events)}"
                )
        except Exception:
            # Prefetch must remain best-effort; context.get_l0 is the auditable
            # tool path when callers need the full structured object.
            pass
        context = self._assembler.build_memory_context(
            user_id=self._user_id, query=query, top_k=5
        )
        if context.items:
            lines.append("[CGM user-memory recall]")
            for item in context.items:
                lines.append(f"- ({item['layer']}) {item['summary']}")
        if not lines:
            # First-run / empty store (F1 A5): guide the agent to gently surface that
            # there is no data yet. The user-facing wording is the agent's, in the
            # informed-companion tone (SOUL / Principle IV) — never a command.
            try:
                if SQLiteCGMRepository(self._store).status().glucose_point_count == 0:
                    lines.append(
                        "[CGM empty store] No CGM data for this user yet. If the user "
                        "asks about their glucose, gently let them know there is no data "
                        "yet and that they can import a CSV (`import-cgm`) or try sample "
                        "data (`seed-demo`) — stay in the informed-companion tone, no "
                        "pressure and no commands."
                    )
            except Exception:
                pass
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
        if self._agent_context != "primary":
            return None
        active_session = session_id or self._session_id
        if not active_session:
            return None
        user_text = user_content.strip()
        if len(user_text) < 8:
            return None
        session_notes = self._session_turns.setdefault(active_session, [])
        if _normalized_text(user_text) in {_normalized_text(note) for note in session_notes}:
            return None
        session_notes.append(user_text[:240])
        if self._extractor is not None:
            candidate = self._extractor.extract(
                user_id=self._user_id,
                session_id=active_session,
                text=user_text,
            )
        else:
            if not _looks_memory_relevant(user_text):
                return None
            candidate = MemoryCandidate(
                candidate_id=_turn_candidate_id(active_session, user_text),
                user_id=self._user_id,
                target_layer=MemoryLayer.L1,
                candidate_type="conversation_note",
                summary=_candidate_summary(user_text),
                requires_user_confirmation=True,
                evidence_refs=[
                    EvidenceRef(
                        kind="memory",
                        ref_id=f"session:{active_session}",
                        summary="Captured from Hermes conversation turn",
                    )
                ],
                created_at=utc_now(),
            )
        if candidate is None:
            return None
        existing = {
            item.candidate_id
            for item in self._repository.list_candidates(self._user_id)
        }
        if candidate.candidate_id not in existing:
            self._repository.enqueue_candidate(candidate)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        # End-of-session is a natural consolidation trigger (D026).
        self._consolidation.consolidate(self._user_id, session_id=self._session_id)
        if self._hermes_home:
            UserMDSyncService(repository=self._repository).sync(
                user_id=self._user_id,
                hermes_home=self._hermes_home,
            )
        self._session_turns.pop(self._session_id, None)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        if reset or rewound:
            self._session_turns.pop(new_session_id, None)
        self._session_id = new_session_id
        if kwargs.get("user_id"):
            self._user_id = str(kwargs["user_id"])
        if kwargs.get("agent_context"):
            self._agent_context = str(kwargs["agent_context"])

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        if kwargs.get("agent_context"):
            self._agent_context = str(kwargs["agent_context"])
        if self._session_id:
            self._session_turns.setdefault(self._session_id, [])

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return copy.deepcopy(MEMORY_TOOL_SCHEMAS)

    def shutdown(self) -> None:
        return None

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        items: list[str] = []
        episodes = self._repository.list_episodes(self._user_id)
        hypotheses = self._repository.list_hypotheses(self._user_id)
        if episodes:
            items.append("Recent episodes:")
            for episode in episodes[-3:]:
                items.append(f"- {episode.summary}")
        if hypotheses:
            items.append("Active hypotheses:")
            for hypothesis in hypotheses[:3]:
                items.append(f"- {hypothesis.state.value}: {hypothesis.statement}")
        turns = self._session_turns.get(self._session_id, [])
        if turns:
            items.append("Recent conversation notes:")
            for note in turns[-3:]:
                items.append(f"- {note}")
        return "\n".join(items)

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if action not in {"add", "replace"}:
            return
        text = _stringify_memory_content(content)
        if not text.strip():
            return
        candidate = MemoryCandidate(
            candidate_id=f"builtin-{hashlib.sha1(f'{target}:{text}'.encode('utf-8')).hexdigest()[:16]}",
            user_id=self._user_id,
            target_layer=MemoryLayer.L1,
            candidate_type="builtin_memory_write",
            summary=_candidate_summary(text),
            requires_user_confirmation=True,
            evidence_refs=[
                EvidenceRef(
                    kind="memory",
                    ref_id=f"builtin:{target}",
                    summary="Mirrored from Hermes built-in memory write",
                )
            ],
            created_at=utc_now(),
        )
        existing = {item.candidate_id for item in self._repository.list_candidates(self._user_id)}
        if candidate.candidate_id not in existing:
            self._repository.enqueue_candidate(candidate)

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs: Any,
    ) -> None:
        note = f"Delegated task: {task.strip()} | Result: {result.strip()}"
        if self._session_id:
            self._session_turns.setdefault(self._session_id, []).append(note[:240])


class ConversationMemoryExtractor(Protocol):
    def extract(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
    ) -> MemoryCandidate | None: ...


def _looks_memory_relevant(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "glucose",
        "blood sugar",
        "cgm",
        "meal",
        "ate",
        "food",
        "exercise",
        "walk",
        "insulin",
        "low",
        "high",
        "hypo",
        "hyper",
        "早餐",
        "午餐",
        "晚餐",
        "血糖",
        "低血糖",
        "高血糖",
        "运动",
        "胰岛素",
        "烦",
        "焦虑",
        "沮丧",
        "累",
        "自责",
        "压力大",
        "心情不好",
        "蛋糕",
        "面条",
        "甜点",
        "水果",
        "奶茶",
        "火锅",
        "甜品",
        "零食",
        "米饭",
        "馒头",
        "睡觉",
        "失眠",
        "睡得晚",
        "睡眠",
        "熬夜",
        "睡不好",
        "药",
        "二甲双胍",
        "打针",
        "吃药",
        "用药",
        "压力",
        "生病",
        "感冒",
        "发烧",
        "不舒服",
        "难受",
    )
    return any(keyword in lowered for keyword in keywords)


def _turn_candidate_id(session_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{text}".encode("utf-8")).hexdigest()
    return f"turn-{digest[:16]}"


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())


def _candidate_summary(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 180:
        return normalized
    return normalized[:177] + "..."


def _stringify_memory_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(content)
