"""Async staged consolidation pipeline L1 -> L2 -> L3 + forgetting.

MEM-ARCH-20260601 §5.2 / DECISION_LOG D026. Consolidation is the highest
-leverage memory component (2026 consensus: Anthropic Dreaming, SCM, Hindsight
observation layer with proof counts). It runs after reports/sessions, NOT inline.

Staging (threshold-gated, never "every episode becomes a profile"):
- ingest candidates -> L1 episodes (accepted candidates only)
- same episode_type recurring on >= L2_MIN_EPISODES distinct days -> L2 belief
  (confidence from evidence_count; conflict -> supersede + lower confidence)
- >= L3_MIN_PATTERN distinct days -> L3 hypothesis state machine
  (candidate -> observing -> stable; contradiction -> archived)
- forgetting: L1 90d idle archive, L2 30d decay (handled by repository helpers)

The actual L1 extraction from raw turns uses a lightweight model in production;
here it is driven deterministically from accepted MemoryCandidates + detected
events so the pipeline is testable offline. A real extractor can be injected.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import (
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
    MemoryCandidate,
    MemorySummary,
)
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository, new_id


@dataclass(frozen=True)
class ConsolidationConfig:
    l2_min_episodes: int = 3          # distinct days of same type -> belief
    l3_min_pattern: int = 3           # distinct days -> hypothesis
    l3_stable_threshold: int = 5      # evidence_count to mark stable
    timezone: str = "Asia/Shanghai"
    l1_archive_idle_days: int = 90
    l2_stale_days: int = 30
    l2_decay: float = 0.2
    l2_deactivate_below: float = 0.3


@dataclass(frozen=True)
class ConsolidationReport:
    episodes_written: int = 0
    profiles_updated: int = 0
    hypotheses_updated: int = 0
    episodes_archived: int = 0
    profiles_decayed: int = 0


class ConsolidationService:
    def __init__(
        self,
        *,
        repository: SQLiteMemoryRepository,
        config: ConsolidationConfig | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or ConsolidationConfig()
        self.audit_service = audit_service

    def ingest_accepted_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        occurred_at: datetime,
        episode_type: str,
        now: datetime | None = None,
    ) -> L1Episode:
        """Promote an accepted L1-targeted candidate into an L1 episode.

        C4: promotion is idempotent per candidate. The episode id is derived
        deterministically from the candidate id, and an existing episode is
        returned instead of inserting a duplicate. This makes a retry safe when a
        crash lands between the L1 write and the candidate status update (the
        confirm path commits these separately).
        """
        now = now or _now()
        episode_id = f"ep-cand-{candidate.candidate_id}"
        existing = self.repository.get_episode(episode_id)
        if existing is not None:
            return existing
        episode = L1Episode(
            episode_id=episode_id,
            user_id=candidate.user_id,
            occurred_at=occurred_at,
            episode_type=episode_type,
            summary=candidate.summary,
            evidence_refs=candidate.evidence_refs,
            source_report_id=candidate.source_report_id,
            source_section_id=candidate.source_section_id,
            confidence=candidate.confidence,
            created_at=now,
            last_referenced_at=now,
        )
        return self.repository.create_episode(episode)

    def consolidate(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        session_id: str | None = None,
    ) -> ConsolidationReport:
        """Run staged L1->L2->L3 consolidation + forgetting for a user."""
        now = now or _now()
        zone = ZoneInfo(self.config.timezone)
        episodes = self.repository.list_episodes(user_id)

        # group active episodes by type -> set of distinct local days
        days_by_type: dict[str, set] = {}
        latest_refs_by_type: dict[str, list] = {}
        for ep in episodes:
            day = ep.occurred_at.astimezone(zone).date()
            days_by_type.setdefault(ep.episode_type, set()).add(day)
            latest_refs_by_type.setdefault(ep.episode_type, [])
            latest_refs_by_type[ep.episode_type].extend(ep.evidence_refs)

        profiles_updated = 0
        hypotheses_updated = 0

        for episode_type, days in days_by_type.items():
            day_count = len(days)
            # L2 belief: same-type recurrence
            if day_count >= self.config.l2_min_episodes:
                profiles_updated += self._upsert_belief(
                    user_id=user_id,
                    key=f"pattern:{episode_type}",
                    episode_type=episode_type,
                    day_count=day_count,
                    now=now,
                )
            # L3 hypothesis: recurring pattern
            if day_count >= self.config.l3_min_pattern:
                hypotheses_updated += self._advance_hypothesis(
                    user_id=user_id,
                    episode_type=episode_type,
                    day_count=day_count,
                    evidence_refs=latest_refs_by_type.get(episode_type, []),
                    now=now,
                )

        episodes_archived = self.repository.archive_stale_episodes(
            now=now, max_idle_days=self.config.l1_archive_idle_days
        )
        profiles_decayed = self.repository.decay_profile_items(
            now=now,
            stale_days=self.config.l2_stale_days,
            decay=self.config.l2_decay,
            deactivate_below=self.config.l2_deactivate_below,
        )
        report = ConsolidationReport(
            profiles_updated=profiles_updated,
            hypotheses_updated=hypotheses_updated,
            episodes_archived=episodes_archived,
            profiles_decayed=profiles_decayed,
        )
        if self.audit_service is not None:
            self.audit_service.log(
                session_id=session_id or "memory-consolidation",
                event_type="memory_consolidation",
                payload={
                    "user_id": user_id,
                    "status": "ok",
                    **asdict(report),
                },
            )
        return report

    def synthesize_state(
        self,
        user_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
        period: str = "weekly",
        metrics_summary: dict | None = None,
        now: datetime | None = None,
    ) -> MemorySummary:
        """Warm "dreaming" (D034): regenerate a structured state digest from
        recent metrics + memory and persist it for prefetch injection.

        Deterministic/templated here so it is testable offline; a lightweight
        model can replace the templating in production. Metrics (TIR, mean, etc.)
        are supplied by the caller (analytics) so this stays decoupled from CGM
        storage.
        """
        now = now or _now()
        label = {"daily": "日", "weekly": "周", "monthly": "月"}.get(period, period)
        metrics = dict(metrics_summary or {})
        if metrics.get("tir_pct") is not None and metrics.get("delta_tir_pct") is None:
            previous = self.repository.latest_summary(user_id, period=period)
            if previous is not None and previous.metrics.get("tir_pct") is not None:
                metrics["delta_tir_pct"] = round(
                    float(metrics["tir_pct"]) - float(previous.metrics["tir_pct"]),
                    2,
                )
        parts: list[str] = []
        if metrics.get("tir_pct") is not None:
            line = f"本{label}目标范围内时间(TIR) {metrics['tir_pct']}%"
            delta = metrics.get("delta_tir_pct")
            if delta is not None:
                line += f",环比{'+' if delta >= 0 else ''}{delta}%"
            parts.append(line + "。")
        if metrics.get("mean_mgdl") is not None:
            parts.append(f"平均血糖约 {metrics['mean_mgdl']} mg/dL。")
        active = [
            h
            for h in self.repository.list_hypotheses(user_id)
            if h.state in (HypothesisState.OBSERVING, HypothesisState.STABLE)
        ]
        if active:
            parts.append("近期模式:" + ";".join(h.statement for h in active[:3]) + "。")
        recent = sorted(
            self.repository.list_episodes(user_id),
            key=lambda e: e.occurred_at,
            reverse=True,
        )[:3]
        if recent:
            parts.append("近期事件:" + ";".join(e.summary for e in recent) + "。")
        content = " ".join(parts) or f"本{label}暂无足够数据形成状态摘要。"
        summary = MemorySummary(
            summary_id=new_id(),
            user_id=user_id,
            period=period,
            window_start=window_start,
            window_end=window_end,
            content=content,
            metrics=metrics,
            created_at=now,
        )
        return self.repository.create_summary(summary)

    def _upsert_belief(
        self,
        *,
        user_id: str,
        key: str,
        episode_type: str,
        day_count: int,
        now: datetime,
    ) -> int:
        existing = self.repository.list_profile_items(user_id, key=key, active_only=False)
        confidence = min(0.95, round(0.4 + 0.1 * day_count, 4))
        # B1: store a human-readable summary alongside the raw count so the
        # USER.md L2 export renders a sentence, not bare JSON (D039).
        value = {
            "recurring_days": day_count,
            "summary": f"近 {day_count} 天反复出现「{episode_type.replace('_', ' ')}」模式",
        }
        if existing:
            item = existing[0]
            item.value = value
            item.confidence = confidence
            item.evidence_count = day_count
            item.last_verified = now
            item.is_active = True
            item.updated_at = now
            self.repository.upsert_profile_item(item)
        else:
            self.repository.upsert_profile_item(
                L2ProfileItem(
                    item_id=new_id(),
                    user_id=user_id,
                    key=key,
                    value=value,
                    confidence=confidence,
                    evidence_count=day_count,
                    last_verified=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        return 1

    def _advance_hypothesis(
        self,
        *,
        user_id: str,
        episode_type: str,
        day_count: int,
        evidence_refs: list,
        now: datetime,
    ) -> int:
        statement = f"Recurring {episode_type.replace('_', ' ')} pattern"
        existing = [
            h
            for h in self.repository.list_hypotheses(user_id)
            if h.statement == statement
        ]
        if day_count >= self.config.l3_stable_threshold:
            state = HypothesisState.STABLE
        else:
            state = HypothesisState.OBSERVING
        if existing:
            hyp = existing[0]
            if hyp.state == HypothesisState.ARCHIVED:
                return 0
            hyp.state = state
            hyp.evidence_count = day_count
            hyp.evidence_refs = evidence_refs
            hyp.last_checked = now
            hyp.updated_at = now
            self.repository.upsert_hypothesis(hyp)
        else:
            self.repository.upsert_hypothesis(
                L3Hypothesis(
                    hypothesis_id=new_id(),
                    user_id=user_id,
                    statement=statement,
                    state=state,
                    evidence_count=day_count,
                    evidence_refs=evidence_refs,
                    last_checked=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        return 1


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
