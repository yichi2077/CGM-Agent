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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from hermes_cgm_agent.domain import (
    DataScope,
    EvidenceRef,
    GlucosePoint,
    GlucoseTrend,
    GlucoseUnit,
    MemoryCandidate,
    MemoryLayer,
    convert_glucose_value,
)
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.analytics import RealtimeSignalConfig
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory.affect import detect_affect
from hermes_cgm_agent.services.memory.assembler import MemoryContextAssembler
from hermes_cgm_agent.services.memory.consolidation import ConsolidationService
from hermes_cgm_agent.services.memory.l0_builder import L0ContextBuilder
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository
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

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_SOUL_PATH = _PROJECT_ROOT / "SOUL.md"
_DEFAULT_SOUL_PROMPT = (
    "CGM persona: act as a 知情陪伴者, not a clinician. Prefer the user's "
    "own CGM history before general knowledge, keep language short and "
    "life-oriented, state uncertainty, avoid judgment and commands, and invite "
    "confirmation when offering a hypothesis."
)
# D1: section-based priority for SOUL.md compaction truncation.
# Lower number = higher priority (kept when truncating).
# Order: 角色定义 > 交互原则 > 语言风格 > 安全边界 > 其他.
_SOUL_SECTION_PRIORITY: list[tuple[str, int]] = [
    ("我是谁", 0),       # 角色定义
    ("交互原则", 1),     # 交互原则
    ("语言风格", 2),     # 语言风格
    ("用户保护规则", 3), # 情感/遗忘边界
    ("安全边界", 3),     # 安全边界
]

# D1: hard character cap for the compacted SOUL.md persona summary.
_SOUL_HARD_CAP = 2500

# D4: GlucoseTrend → Chinese label for real-time status injection.
_TREND_CN: dict[GlucoseTrend, str] = {
    GlucoseTrend.RISING_FAST: "快速上升",
    GlucoseTrend.RISING: "上升",
    GlucoseTrend.STABLE: "平稳",
    GlucoseTrend.FALLING: "下降",
    GlucoseTrend.FALLING_FAST: "快速下降",
    GlucoseTrend.UNKNOWN: "未知",
}


class CGMMemoryProvider:
    """Hermes-compatible provider (duck-typed). Carries L1 + L3."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        user_id: str | None = None,
        extractor: "ConversationMemoryExtractor | None" = None,
    ) -> None:
        from hermes_cgm_agent.config import default_user_id

        self._store = store
        # P0-1 (D052): default to the deployment-wide identity so the memory
        # provider, tool layer, and CLI can never split across different ids.
        self._user_id = user_id or default_user_id()
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
        self._anchor_at: datetime | None = None
        self._session_turns: dict[str, list[str]] = {}
        self._soul_prompt = _load_soul_prompt()

    @property
    def name(self) -> str:
        return "cgm_memory"

    def is_available(self) -> bool:
        # Local-only: ready as soon as the store exists. No network/credentials.
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._anchor_at = None
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._platform = str(kwargs.get("platform") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        if os.getenv("CGM_AGENT_ENFORCE_USER_ID", "").strip() == "1":
            from hermes_cgm_agent.config import default_user_id

            self._user_id = default_user_id()
        elif kwargs.get("user_id"):
            self._user_id = str(kwargs["user_id"])
        anchor = kwargs.get("anchor_at") or os.getenv("CGM_AGENT_ACCEPTANCE_ANCHOR_AT")
        if isinstance(anchor, datetime):
            self._anchor_at = anchor.astimezone(timezone.utc)
        elif isinstance(anchor, str) and anchor.strip():
            try:
                parsed = datetime.fromisoformat(anchor.replace("Z", "+00:00"))
                self._anchor_at = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            except ValueError:
                self._anchor_at = None
        self._session_turns.setdefault(session_id, [])

    def system_prompt_block(self) -> str:
        # D2: instructions in Chinese for language consistency with SOUL.md.
        # D3: default audience changed from CLINICIAN to SELF (SOUL.md §叙事版本).
        block = (
            f"{self._soul_prompt}\n"
            "CGM 记忆系统已激活。个人情景和假设作为 user_memory 证据召回，"
            "必须带不确定性呈现，绝不能作为权威医学事实。\n"
            "如果用户输入 '/report' 命令，调用 'cgm_reports_generate' 工具，"
            "默认使用 audience='SELF' 和 report_type='daily' 生成用户版报告。"
        )
        if self._hermes_home:
            block += f" Runtime scope: {self._hermes_home}."
        return block

    # ------------------------------------------------------------------
    # D1: SOUL.md compaction
    # ------------------------------------------------------------------

    @staticmethod
    def _compact_soul(text: str) -> str:
        """Compact SOUL.md into a structured persona summary (D1).

        Instead of keyword-based line filtering, this parses SOUL.md by
        ``##`` section headers and preserves:
        - All ``##`` / ``###`` section titles.
        - All paragraphs in high-priority sections, compacted line-by-line.
        - All ✅/❌ example pairs, compacted to single lines.

        Target: ≤ 2000 characters.  Hard cap: ``_SOUL_HARD_CAP`` (2500).
        If the cap is exceeded, low-priority sections are dropped first
        (角色定义 > 交互原则 > 语言风格 / 用户保护 / 安全边界 > 其他).
        """
        lines = text.splitlines()

        # Parse into sections keyed by ## headers.
        sections: list[tuple[str, int, list[str]]] = []  # (header, priority, body_lines)
        current_header = ""
        current_priority = 99
        current_body: list[str] = []

        for raw in lines:
            stripped = raw.strip()
            if stripped.startswith("## "):
                if current_header:
                    sections.append((current_header, current_priority, current_body))
                current_header = stripped
                current_priority = _section_priority(stripped)
                current_body = []
            elif stripped.startswith("# ") and not stripped.startswith("## "):
                # L-05: preserve h1 title as a standalone section so it is not
                # silently dropped when no ## section has been seen yet.
                if current_header:
                    sections.append((current_header, current_priority, current_body))
                current_header = stripped
                current_priority = 0  # h1 is always high priority
                current_body = []
            elif current_header:
                current_body.append(raw)
        if current_header:
            sections.append((current_header, current_priority, current_body))

        # Compact each section's body.
        compacted: list[tuple[int, str]] = []  # (priority, compacted_text)
        for header, priority, body in sections:
            # The full protection section is longer than the prompt budget, but
            # its emotional-first and memory-boundary commitments are mandatory
            # persona rules. Keep a deterministic compact form rather than
            # dropping it based on arbitrary document order.
            compacted_text = (
                _compact_user_protection_section(header, body)
                if "用户保护规则" in header or "安全边界" in header
                else _compact_section_body(header, body)
            )
            if compacted_text.strip():
                compacted.append((priority, compacted_text))

        # Build output. If under hard cap, return as-is (original order).
        result = "\n\n".join(text_part for _, text_part in compacted)
        if len(result) <= _SOUL_HARD_CAP:
            return result

        # Over hard cap: drop lowest-priority sections first (highest priority
        # number = lowest priority = drop first), preserving original order.
        drop_order = sorted(range(len(compacted)), key=lambda i: -compacted[i][0])
        kept = set(range(len(compacted)))
        for idx in drop_order:
            if len(kept) <= 1:
                break  # Always keep at least one section.
            kept.discard(idx)
            result = "\n\n".join(
                compacted[i][1] for i in range(len(compacted)) if i in kept
            )
            if len(result) <= _SOUL_HARD_CAP:
                break

        return result[:_SOUL_HARD_CAP]

    # ------------------------------------------------------------------
    # D4: Real-time CGM status
    # ------------------------------------------------------------------

    def _build_realtime_status(self) -> str:
        """Build a structured real-time CGM status summary in Chinese (D4).

        Queries the most recent glucose points (last 1 hour), computes the
        current value, trend direction, and range status.  Returns an empty
        string when the store is completely empty — the empty-store hint in
        :meth:`prefetch` handles that case.
        """
        try:
            cgm_repo = SQLiteCGMRepository(self._store)
            now = self._anchor_at or utc_now()
            window_start = now - timedelta(hours=1)
            scope = DataScope(
                user_id=self._user_id,
                window_start=window_start,
                window_end=now,
            )
            points = cgm_repo.list_glucose_points(scope)
        except Exception:
            return ""

        # Match the canonical realtime service: only valid readings can be
        # presented to the LLM as the user's current physiological state.
        valid_points = sorted(
            (point for point in points if str(point.quality_flag) == "valid"),
            key=lambda point: point.timestamp,
        )
        if not valid_points:
            if not self._has_glucose_points_for_user():
                # Preserve the separate empty-store guidance in prefetch().
                return ""
            return "当前状态：最近 1 小时无新数据，可能传感器未连接。"

        latest = valid_points[-1]
        stale_after = timedelta(minutes=RealtimeSignalConfig().stale_after_minutes)
        age = now - latest.timestamp
        if age > stale_after:
            age_minutes = max(1, round(age.total_seconds() / 60))
            return f"当前状态：最近有效读数距今约 {age_minutes} 分钟，当前数据可能已过期。"
        value_mg_dl = latest.value_mg_dl
        value_mmol = round(
            convert_glucose_value(value_mg_dl, GlucoseUnit.MG_DL, GlucoseUnit.MMOL_L), 1
        )

        # Determine trend: prefer the point's trend field, fall back to
        # computing from the delta between the latest and an earlier point.
        trend_text = _TREND_CN.get(latest.trend, "未知")
        if latest.trend == GlucoseTrend.UNKNOWN:
            trend_text = _compute_trend_from_points(valid_points)

        # Determine range status (AGP 2019 consensus thresholds).
        if value_mg_dl < 54:
            range_text = "处于严重低血糖范围"
        elif value_mg_dl < 70:
            range_text = "偏低，低于目标范围"
        elif value_mg_dl <= 180:
            range_text = "处于目标范围内"
        elif value_mg_dl <= 250:
            range_text = "偏高，高于目标范围"
        else:
            range_text = "处于严重高血糖范围"

        return (
            f"当前状态：最近读数 {value_mmol:.1f} mmol/L"
            f"（约 {value_mg_dl:.0f} mg/dL），"
            f"趋势{trend_text}，{range_text}。"
        )

    def _has_glucose_points_for_user(self) -> bool:
        """Return whether this provider's user has any CGM history at all."""
        try:
            with self._store.connect() as conn:
                return conn.execute(
                    "SELECT 1 FROM glucose_points WHERE user_id = ? LIMIT 1",
                    (self._user_id,),
                ).fetchone() is not None
        except Exception:
            return False

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        lines: list[str] = []
        # P1-5 (MVP audit): deterministic emotional-first orchestration. When
        # the user's message carries distress vocabulary, lead the injected
        # context with an empathy directive and reduce data-injection strength
        # (skip warm summary + L0 stats, cap memory recall) so the LLM's first
        # move is acknowledgement, not numbers. Realtime status stays — safety
        # visibility is never traded away.
        affect_terms = detect_affect(query)
        affect_hit = bool(affect_terms)
        if affect_hit:
            lines.append(
                "[CGM 情感优先] 用户此刻的话里带着情绪（"
                + "、".join(affect_terms[:3])
                + "）。先回应感受，再谈数据；这一轮少给数字，多给陪伴。"
            )
        # D4: real-time CGM status (current value, trend, range).
        realtime = self._build_realtime_status()
        if realtime:
            lines.append(f"[CGM 实时状态] {realtime}")
        # Warm state summary ("dreaming", D034) injected first as background.
        latest = self._repository.latest_summary(self._user_id)
        if latest is not None and not affect_hit:
            lines.append(f"[CGM state summary] {latest.content}")
        if not affect_hit:
            try:
                l0 = L0ContextBuilder(
                    repository=SQLiteCGMRepository(self._store),
                ).build(user_id=self._user_id, anchor_at=self._anchor_at)
                if l0.window_summary.point_count or l0.key_glucose_events:
                    lines.append(
                        "[CGM L0 context] "
                        f"{l0.window.span_days}d points={l0.window_summary.point_count}, "
                        f"recent_points={len(l0.high_res_recent)}, "
                        f"hourly={len(l0.mid_far_hourly)}, "
                        f"events={len(l0.key_glucose_events)}"
                    )
                elif latest is not None:
                    lines.append(
                        "[CGM L0 context unavailable] No recent glucose points were found "
                        "in the current L0 window; any CGM state summary above may be stale."
                    )
            except Exception:
                # Prefetch must remain best-effort; context.get_l0 is the auditable
                # tool path when callers need the full structured object.
                pass
        context = self._assembler.build_memory_context(
            user_id=self._user_id, query=query, top_k=2 if affect_hit else 5
        )
        if context.items:
            lines.append("[CGM user-memory recall]")
            # D058: dedup identical summaries so repeated life-language episodes
            # ("晚上血糖回落得比较快" ×3) don't crowd the injected context.
            seen_summaries: set[str] = set()
            for item in context.items:
                summary = item["summary"]
                if summary in seen_summaries:
                    continue
                seen_summaries.add(summary)
                lines.append(f"- ({item['layer']}) {summary}")
        if not lines:
            # First-run / empty store (F1 A5): guide the agent to gently surface that
            # there is no data yet. The user-facing wording is the agent's, in the
            # informed-companion tone (SOUL / Principle IV) — never a command.
            try:
                if not self._has_glucose_points_for_user():
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
        # M-07: wrap check-then-insert in a transaction to eliminate the
        # TOCTOU race where a concurrent turn could enqueue the same
        # candidate_id between the list and the insert.
        with self._store.transaction():
            existing = {
                item.candidate_id
                for item in self._repository.list_candidates(self._user_id)
            }
            if candidate.candidate_id not in existing:
                self._repository.enqueue_candidate(candidate)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        # End-of-session is a natural consolidation trigger (D026).
        # H-06: wrap in try/finally so a consolidation or sync failure
        # does not leak the session entry in _session_turns.
        try:
            self._consolidation.consolidate(self._user_id, session_id=self._session_id)
            if self._hermes_home:
                UserMDSyncService(repository=self._repository).sync(
                    user_id=self._user_id,
                    hermes_home=self._hermes_home,
                )
        except Exception:
            import logging
            logging.getLogger("hermes_cgm_agent.memory").exception(
                "Consolidation failed on session end"
            )
        finally:
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
        if os.getenv("CGM_AGENT_ENFORCE_USER_ID", "").strip() == "1":
            from hermes_cgm_agent.config import default_user_id

            self._user_id = default_user_id()
        elif kwargs.get("user_id"):
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
    lowered = text.casefold()
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
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "rice",
        "noodle",
        "bread",
        "pizza",
        "burger",
        "sandwich",
        "soup",
        "salad",
        "coffee",
        "tea",
        "juice",
        "soda",
        "beer",
        "wine",
        "chocolate",
        "cookie",
        "cake",
        "ice cream",
        "fruit",
        "vegetable",
        "meat",
        "fish",
        "chicken",
        "beef",
        "pork",
        "tofu",
        "dumpling",
        "porridge",
        "oatmeal",
        "dessert",
        "早餐",
        "午餐",
        "晚餐",
        "加餐",
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
        # F3: expanded food vocabulary for broader memory-relevance detection.
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
        "面包",
        "饺子",
        "包子",
        "粥",
        "米粉",
        "汉堡",
        "披萨",
        "寿司",
        "沙拉",
        "汤",
        "炒饭",
        "炒面",
        "油条",
        "豆浆",
        "牛奶",
        "酸奶",
        "鸡蛋",
        "牛肉",
        "猪肉",
        "鸡肉",
        "鱼",
        "虾",
        "豆腐",
        "蔬菜",
        "土豆",
        "番薯",
        "玉米",
        "燕麦",
        "饼干",
        "巧克力",
        "冰淇淋",
        "可乐",
        "果汁",
        "啤酒",
        "红酒",
        "咖啡",
        "茶",
        "蜂蜜",
        "花生",
        "坚果",
        "瓜子",
        "汤圆",
        "月饼",
        "粽子",
        "年糕",
        "凉皮",
        "肉夹馍",
        "煎饼",
        "麻辣烫",
        "烧烤",
        "串串",
        "炸鸡",
        "薯条",
        "薯片",
        "花卷",
        "烧饼",
        "吃饱",
        "吃多",
        "吃少",
        "没吃",
        "饿",
        "馋",
        "碳水",
        "糖分",
        "热量",
        "饱了",
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
    return any(_contains_memory_term(lowered, keyword) for keyword in keywords)


def _section_priority(header: str) -> int:
    """Determine section priority for SOUL.md truncation (D1).

    Lower number = higher priority (kept when truncating).
    Order: 角色定义 > 交互原则 > 语言风格 > 安全边界 > 其他.
    """
    for keyword, priority in _SOUL_SECTION_PRIORITY:
        if keyword in header:
            return priority
    return 4  # 其他


def _is_table_separator(line: str) -> bool:
    """Check if a line is a Markdown table separator (e.g., ``|---|---|---|``)."""
    if not line.startswith("|"):
        return False
    content = line.replace("|", "").replace("-", "").replace(" ", "").replace(":", "")
    return content == ""


# D1: per-line character cap for compacted SOUL.md paragraphs. Keeps each
# line concise so the total stays within _SOUL_HARD_CAP while preserving
# key persona definitions ("我不是监督者", "我不是同谋者" …) that appear in
# later paragraphs.
_COMPACT_LINE_LIMIT = 100


def _truncate_line(line: str, limit: int = _COMPACT_LINE_LIMIT) -> str:
    """Truncate a line to ``limit`` characters, appending an ellipsis if cut."""
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def _compact_section_body(header: str, body_lines: list[str]) -> str:
    """Compact a single SOUL.md section body (D1).

    Keeps ``###`` headers, ALL paragraphs under each header (not just the
    first), and all ✅/❌ example pairs (compacted to single lines). Each
    regular content line is truncated to ``_COMPACT_LINE_LIMIT`` characters
    to control total length while preserving key persona definitions (e.g.
    "我不是监督者", "我不是同谋者") that appear in paragraphs after the first.
    """
    result: list[str] = [header]
    pending_check: str | None = None

    for raw in body_lines:
        stripped = raw.strip()

        if not stripped or stripped == "---":
            # Blank line / separator: flush pending ✅.
            if pending_check is not None:
                result.append(pending_check)
                pending_check = None
            continue

        if stripped.startswith("### "):
            if pending_check is not None:
                result.append(pending_check)
                pending_check = None
            result.append(stripped)
            continue

        # Skip table separator lines (e.g., |---|---|---|).
        if _is_table_separator(stripped):
            continue

        if stripped.startswith("✅"):
            if pending_check is not None:
                # Previous ✅ had no matching ❌ — flush it.
                result.append(pending_check)
            pending_check = _truncate_line(stripped)
            continue

        if stripped.startswith("❌"):
            if pending_check is not None:
                result.append(f"{pending_check} | {_truncate_line(stripped)}")
                pending_check = None
            else:
                result.append(_truncate_line(stripped))
            continue

        # Regular content line: keep ALL paragraphs (not just the first),
        # truncating each to _COMPACT_LINE_LIMIT to control total length.
        result.append(_truncate_line(stripped))
        pending_check = None

    # Flush any remaining pending ✅.
    if pending_check is not None:
        result.append(pending_check)

    return "\n".join(result)


def _compact_user_protection_section(header: str, body_lines: list[str]) -> str:
    """Dynamically compact the SOUL user-protection / safety-boundary section.

    Previously this function returned hardcoded content, which could drift
    from the actual SOUL.md text.  Now it dynamically extracts ``###``
    sub-headers and the first content line under each from the real SOUL.md
    section body, keeping the output compact enough to survive the hard cap
    while still being derived from the source document (M-05).
    """
    result: list[str] = [header]
    in_subsection = False
    got_first_line = False
    for raw in body_lines:
        stripped = raw.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("### "):
            result.append(stripped)
            in_subsection = True
            got_first_line = False
            continue
        if stripped.startswith("## "):
            # A new ## section inside the body — stop.
            break
        if in_subsection and not got_first_line:
            # Skip ✅/❌ example pairs; keep only the first prose line.
            if stripped.startswith(("✅", "❌", "|")):
                continue
            result.append(_truncate_line(stripped))
            got_first_line = True
    return "\n".join(result)


def _compute_trend_from_points(points: list[GlucosePoint]) -> str:
    """Compute trend direction from recent glucose points (D4).

    Compares the latest reading with a reference point ~15 min earlier.
    Uses rate of change (mg/dL/min) instead of absolute delta so the
    threshold is time-normalized: a 20 mg/dL rise over 5 minutes is
    "fast", but the same rise over 30 minutes is only "rising".
    """
    if len(points) < 2:
        return "未知"

    last = points[-1]
    # Find a reference point ~15 min before the last reading.
    ref = points[0]
    for p in reversed(points[:-1]):
        time_diff = (last.timestamp - p.timestamp).total_seconds()
        if time_diff >= 900:  # 15 minutes
            ref = p
            break

    delta = last.value_mg_dl - ref.value_mg_dl
    minutes_between = (last.timestamp - ref.timestamp).total_seconds() / 60.0
    if minutes_between <= 0:
        return "未知"

    # D4: rate-of-change thresholds (mg/dL per minute).
    delta_per_min = delta / minutes_between

    if delta_per_min > 2:
        return "快速上升"
    elif delta_per_min > 0.5:
        return "上升"
    elif delta_per_min < -2:
        return "快速下降"
    elif delta_per_min < -0.5:
        return "下降"
    else:
        return "平稳"


def _load_soul_prompt() -> str:
    """Load and compact SOUL.md into a structured persona summary (D1).

    Instead of keyword-based line filtering, this reads the full SOUL.md and
    compacts it by section via :meth:`CGMMemoryProvider._compact_soul`.
    """
    try:
        text = _SOUL_PATH.read_text(encoding="utf-8")
    except OSError:
        return _DEFAULT_SOUL_PROMPT

    compact = CGMMemoryProvider._compact_soul(text)
    if not compact.strip():
        return _DEFAULT_SOUL_PROMPT

    return f"SOUL.md 人设摘要：\n{compact}"


def _turn_candidate_id(session_id: str, text: str) -> str:
    digest = hashlib.sha1(f"{session_id}:{text}".encode("utf-8")).hexdigest()
    return f"turn-{digest[:16]}"


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())


def _contains_memory_term(text: str, term: str) -> bool:
    """Match ASCII terms as words while retaining natural CJK substring search."""
    normalized_term = term.casefold()
    if normalized_term.isascii() and any(char.isalnum() for char in normalized_term):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            text.casefold(),
        ) is not None
    return normalized_term in text.casefold()


# F3: food-specific keywords for episode summary enrichment. When a long
# conversation turn is truncated for the candidate summary, any food names
# appearing after the truncation point are appended so BM25 can recall
# episodes by food name even when the food mention is past the cut.
_FOOD_NAMES_FOR_SUMMARY: tuple[str, ...] = (
    "面条", "米饭", "馒头", "面包", "饺子", "包子", "粥", "米粉",
    "汉堡", "披萨", "寿司", "沙拉", "汤", "炒饭", "炒面", "油条",
    "豆浆", "牛奶", "酸奶", "鸡蛋", "牛肉", "猪肉", "鸡肉", "鱼",
    "虾", "豆腐", "蔬菜", "土豆", "番薯", "玉米", "燕麦", "饼干",
    "巧克力", "冰淇淋", "可乐", "果汁", "啤酒", "红酒", "咖啡",
    "茶", "蜂蜜", "花生", "坚果", "瓜子", "汤圆", "月饼", "粽子",
    "年糕", "凉皮", "肉夹馍", "煎饼", "麻辣烫", "烧烤", "串串",
    "炸鸡", "薯条", "薯片", "蛋糕", "甜品", "甜点", "水果", "奶茶",
    "火锅", "零食", "noodle", "rice", "bread", "pizza", "burger",
    "cake", "dessert", "fruit", "snack", "chocolate", "cookie",
    "ice cream", "coffee", "tea", "juice", "soda", "beer", "wine",
)


def _candidate_summary(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 180:
        return normalized
    truncated = normalized[:177] + "..."
    # F3: preserve food names that appear after the truncation point so
    # BM25 can recall episodes by food name (e.g. a long turn mentioning
    # "...然后下午又吃了饺子" should still be searchable by "饺子").
    full_text = normalized.casefold()
    visible_text = normalized[:177].casefold()
    found = [
        kw for kw in _FOOD_NAMES_FOR_SUMMARY
        if _contains_memory_term(full_text, kw)
        and not _contains_memory_term(visible_text, kw)
    ]
    if found:
        truncated += " 食物:" + ",".join(found[:5])
    return truncated


def _stringify_memory_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(content)
