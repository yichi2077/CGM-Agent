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

from dataclasses import asdict, dataclass, field
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
from hermes_cgm_agent.config import default_timezone
from hermes_cgm_agent.services.memory.repository import SQLiteMemoryRepository, new_id


@dataclass(frozen=True)
class ConsolidationConfig:
    l2_min_episodes: int = 3          # distinct days of same type -> belief
    l3_min_pattern: int = 3           # distinct days -> hypothesis
    l3_stable_threshold: int = 5      # evidence_count to mark stable
    # B1: contradiction + forgetting thresholds.
    l3_contradiction_threshold: int = 3   # contradictory episodes to downgrade STABLE
    l3_consecutive_contra_limit: int = 3  # consecutive contradicting runs to archive
    l3_decay_idle_days: int = 90      # idle days -> STABLE downgraded to OBSERVING
    l3_archive_idle_days: int = 180   # idle days -> valid_to closed (ARCHIVED)
    timezone: str = field(default_factory=default_timezone)
    l1_archive_idle_days: int = 90
    l2_stale_days: int = 30
    l2_decay: float = 0.2
    l2_deactivate_below: float = 0.3
    # B5: number of memory summaries to retain per user.
    summary_keep_count: int = 30


@dataclass(frozen=True)
class ConsolidationReport:
    episodes_written: int = 0
    profiles_updated: int = 0
    hypotheses_updated: int = 0
    episodes_archived: int = 0
    profiles_decayed: int = 0
    hypotheses_decayed: int = 0   # B1: L3 decay/archive count
    candidates_purged: int = 0    # B4: expired candidate queue entries


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

        profiles_updated = 0
        hypotheses_updated = 0

        # B3: wrap all write operations in a single transaction so a crash
        # mid-consolidation cannot leave partial writes (M4).
        with self.repository.store.transaction():
            # First retire stale L1 records.  Otherwise a run can promote
            # evidence into L2/L3 and archive the same evidence moments later.
            episodes_archived = self.repository.archive_stale_episodes(
                now=now, max_idle_days=self.config.l1_archive_idle_days
            )
            # B4: stale candidates are part of the same atomic consolidation
            # unit as promotion/forgetting.
            candidates_purged = self.repository.purge_expired_candidates(now=now)
            episodes = self.repository.list_episodes(user_id)

            # Group active episodes by type -> distinct local days, evidence,
            # and durable source lineage for the L2/L3 records they support.
            days_by_type: dict[str, set] = {}
            latest_refs_by_type: dict[str, list] = {}
            episode_ids_by_type: dict[str, list[str]] = {}
            for ep in episodes:
                day = ep.occurred_at.astimezone(zone).date()
                days_by_type.setdefault(ep.episode_type, set()).add(day)
                latest_refs_by_type.setdefault(ep.episode_type, [])
                latest_refs_by_type[ep.episode_type].extend(ep.evidence_refs)
                episode_ids_by_type.setdefault(ep.episode_type, []).append(ep.episode_id)

            for episode_type, days in days_by_type.items():
                day_count = len(days)
                # L2 belief: same-type recurrence
                if day_count >= self.config.l2_min_episodes:
                    profiles_updated += self._upsert_belief(
                        user_id=user_id,
                        key=f"pattern:{episode_type}",
                        episode_type=episode_type,
                        day_count=day_count,
                        source_episode_ids=episode_ids_by_type.get(episode_type, []),
                        now=now,
                    )
                # L3 hypothesis: recurring pattern
                if day_count >= self.config.l3_min_pattern:
                    hypotheses_updated += self._advance_hypothesis(
                        user_id=user_id,
                        episode_type=episode_type,
                        day_count=day_count,
                        evidence_refs=latest_refs_by_type.get(episode_type, []),
                        source_episode_ids=episode_ids_by_type.get(episode_type, []),
                        now=now,
                        episodes=episodes,
                    )
            profiles_decayed = self.repository.decay_profile_items(
                now=now,
                stale_days=self.config.l2_stale_days,
                decay=self.config.l2_decay,
                deactivate_below=self.config.l2_deactivate_below,
            )
            # B1: L3 forgetting — decay/archive hypotheses with no recent
            # evidence (90d decay, 180d archive).
            hypotheses_decayed = self.decay_hypotheses(user_id, now)

        report = ConsolidationReport(
            profiles_updated=profiles_updated,
            hypotheses_updated=hypotheses_updated,
            episodes_archived=episodes_archived,
            profiles_decayed=profiles_decayed,
            hypotheses_decayed=hypotheses_decayed,
            candidates_purged=candidates_purged,
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
            parts.append(_format_mean_glucose(metrics["mean_mgdl"]))
        active = [
            h
            for h in self.repository.list_hypotheses(user_id)
            if h.state in (HypothesisState.OBSERVING, HypothesisState.STABLE)
        ]
        if active:
            # Life-language (D052): raw statements are English tech jargon
            # ("Recurring rapid rise pattern") and leak into the conversation
            # context via prefetch. Same translation the report narrative uses.
            from hermes_cgm_agent.services.reports.narrative_templates import describe_behavior

            parts.append(
                "近期模式:" + ";".join(describe_behavior(h.statement) for h in active[:3]) + "。"
            )
        recent = sorted(
            self.repository.list_episodes(user_id),
            key=lambda e: e.occurred_at,
            reverse=True,
        )
        # D058: dedup identical life-language summaries — repeating "晚上血糖回落
        # 得比较快" three times is noise for the reader; keep the first 3 distinct.
        recent_unique: list[str] = []
        for episode in recent:
            if episode.summary not in recent_unique:
                recent_unique.append(episode.summary)
            if len(recent_unique) >= 3:
                break
        if recent_unique:
            parts.append("近期事件:" + ";".join(recent_unique) + "。")
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
        # H-07: wrap create_summary + purge in a transaction so a crash
        # between them cannot leave a new summary without purging old ones
        # (or vice versa).
        with self.repository.store.transaction():
            result = self.repository.create_summary(summary)
            # B5: prevent unbounded summary growth — keep only the most recent N.
            self.repository.purge_old_summaries(
                user_id, keep_count=self.config.summary_keep_count
            )
        return result

    def _upsert_belief(
        self,
        *,
        user_id: str,
        key: str,
        episode_type: str,
        day_count: int,
        source_episode_ids: list[str],
        now: datetime,
    ) -> int:
        existing = self.repository.list_profile_items(user_id, key=key, active_only=False)
        confidence = min(0.95, round(0.4 + 0.1 * day_count, 4))
        # B1: store a human-readable summary alongside the raw count so the
        # USER.md L2 export renders a sentence, not bare JSON (D039). The
        # episode type goes through the shared life-language map (D053) so
        # USER.md and recall lines say 「偏高片段」, not 「hyper」.
        from hermes_cgm_agent.services.reports.narrative_templates import describe_behavior

        value = {
            "recurring_days": day_count,
            "summary": f"近 {day_count} 天反复出现「{describe_behavior(episode_type)}」模式",
        }
        if existing:
            item = existing[0]
            item.value = value
            item.confidence = confidence
            item.evidence_count = day_count
            item.last_verified = now
            item.source_episode_ids = source_episode_ids
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
                    source_episode_ids=source_episode_ids,
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
        source_episode_ids: list[str],
        now: datetime,
        episodes: list[L1Episode] | None = None,
    ) -> int:
        statement = f"Recurring {episode_type.replace('_', ' ')} pattern"
        # active_only=True (default) so superseded/archived hypotheses with
        # valid_to set are not re-activated (B2).
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

            # B1: contradiction detection — check whether recent episodes
            # contradict the hypothesis's claimed pattern.
            recent = episodes if episodes is not None else self.repository.list_episodes(user_id)
            contra_episodes = self._detect_contradiction(hyp, recent)

            if len(contra_episodes) >= self.config.l3_contradiction_threshold:
                # Accumulate consecutive contradicting runs; reset to 0 only
                # when a run finds NO contradictions (below threshold).
                hyp.contra_count = (hyp.contra_count or 0) + 1
                # STABLE + contradiction evidence >= N -> downgrade to OBSERVING
                if hyp.state == HypothesisState.STABLE:
                    hyp.state = HypothesisState.OBSERVING
                # Consecutive M contradictions -> ARCHIVED (valid_to closed)
                if hyp.contra_count >= self.config.l3_consecutive_contra_limit:
                    hyp.state = HypothesisState.ARCHIVED
                    hyp.valid_to = now
            else:
                # No contradiction this run — reset the consecutive counter.
                hyp.contra_count = 0
                hyp.state = state

            hyp.evidence_count = day_count
            hyp.evidence_refs = evidence_refs
            # C-02: detect genuinely new episodes before overwriting the list.
            new_episode_ids = set(source_episode_ids) - set(hyp.source_episode_ids)
            hyp.source_episode_ids = source_episode_ids
            if contra_episodes:
                hyp.contra_episode_ids = list(dict.fromkeys(
                    [*hyp.contra_episode_ids, *(ep.episode_id for ep in contra_episodes)]
                ))
            hyp.last_checked = now
            # C-02: only update last_evidence_added when genuinely new
            # episodes are added, not on every consolidate() call.
            # Otherwise idle_days is always 0 and decay never triggers.
            if new_episode_ids or hyp.last_evidence_added is None:
                hyp.last_evidence_added = now
            hyp.updated_at = now
            self.repository.upsert_hypothesis(hyp)
        else:
            # Existing opposite episodes were already visible when this
            # hypothesis was formed.  Seed their IDs so the next identical
            # consolidation is not misclassified as a fresh contradictory
            # run.  Later-arriving historical episodes have new IDs and still
            # count once, which is why a timestamp cursor is insufficient.
            visible_episodes = (
                episodes if episodes is not None else self.repository.list_episodes(user_id)
            )
            self.repository.upsert_hypothesis(
                L3Hypothesis(
                    hypothesis_id=new_id(),
                    user_id=user_id,
                    statement=statement,
                    state=state,
                    evidence_count=day_count,
                    evidence_refs=evidence_refs,
                    source_episode_ids=source_episode_ids,
                    contra_episode_ids=self._opposite_episode_ids(statement, visible_episodes),
                    last_checked=now,
                    last_evidence_added=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        return 1

    # -- B1: contradiction + forgetting --------------------------------------

    # Opposite episode-type pairs — a hypothesis claiming one pattern is
    # contradicted by episodes of the opposite pattern.
    # H-04: added rapid_rise <-> rapid_fall pair.
    _OPPOSITE_TYPES: dict[str, str] = {
        "hyper": "hypo",
        "hypo": "hyper",
        "rapid_rise": "rapid_fall",
        "rapid_fall": "rapid_rise",
    }

    def _detect_contradiction(
        self,
        hypothesis: L3Hypothesis,
        recent_episodes: list[L1Episode],
    ) -> list[L1Episode]:
        """B1: count recent episodes that contradict the hypothesis pattern.

        E.g., if the hypothesis claims a recurring ``hyper`` pattern but recent
        episodes are predominantly ``hypo``, each hypo episode is a piece of
        contradictory evidence. Returns the contradictory episode count.

        Idempotency is keyed by durable L1 episode IDs, not clinical occurrence
        time.  A source can ingest historical readings after a hypothesis was
        last checked; those episodes must count once, while repeated
        consolidation must not count them again.
        """
        opposite_type = self._opposite_type_for_statement(hypothesis.statement)
        if not opposite_type:
            return []
        seen_ids = set(hypothesis.contra_episode_ids)
        # H-04/Codex: use exact type match, not substring, to avoid
        # "hyper" matching "hyperglycemia" etc.
        opposite_normalized = opposite_type.replace("_", "")
        return [
            ep
            for ep in recent_episodes
            if ep.episode_type.lower().replace("_", "") == opposite_normalized
            and ep.episode_id not in seen_ids
        ]

    def _opposite_episode_ids(
        self,
        statement: str,
        episodes: list[L1Episode],
    ) -> list[str]:
        opposite_type = self._opposite_type_for_statement(statement)
        if not opposite_type:
            return []
        # H-04/Codex: exact type match, not substring.
        opposite_normalized = opposite_type.replace("_", "")
        return [
            episode.episode_id
            for episode in episodes
            if episode.episode_type.lower().replace("_", "") == opposite_normalized
        ]

    def _opposite_type_for_statement(self, statement: str) -> str | None:
        """H-04/Codex: use precise pattern matching instead of bare substring.

        ``"hyper" in lowered`` would match ``"hyperglycemia"``, causing false
        positives.  Instead, match the full statement pattern
        ``"recurring {type} pattern"`` with underscores converted to spaces.
        """
        lowered = statement.lower()
        for htype, otype in self._OPPOSITE_TYPES.items():
            pattern = f"recurring {htype.replace('_', ' ')} pattern"
            if pattern in lowered:
                return otype
        return None

    def decay_hypotheses(self, user_id: str, now: datetime) -> int:
        """B1: L3 forgetting — decay and archive hypotheses with no recent
        evidence.

        - ``l3_decay_idle_days`` (90d) without new evidence: a STABLE
          hypothesis is downgraded to OBSERVING (confidence decay).
        - ``l3_archive_idle_days`` (180d) without new evidence: ``valid_to``
          is closed and the state set to ARCHIVED.
        """
        decayed = 0
        # active_only=True so already-archived hypotheses are skipped.
        hyps = self.repository.list_hypotheses(user_id, active_only=True)
        for hyp in hyps:
            if hyp.state == HypothesisState.ARCHIVED:
                continue
            # C-02: decay based on when the last *new evidence* was added,
            # not when the hypothesis was last inspected.  ``last_checked``
            # is updated every consolidate() call, so using it would make
            # idle_days always 0 and decay would never trigger.
            evidence_time = hyp.last_evidence_added or hyp.last_checked
            idle_days = (now - evidence_time).days
            if idle_days >= self.config.l3_archive_idle_days:
                hyp.state = HypothesisState.ARCHIVED
                hyp.valid_to = now
                hyp.updated_at = now
                self.repository.upsert_hypothesis(hyp)
                decayed += 1
            elif idle_days >= self.config.l3_decay_idle_days:
                if hyp.state == HypothesisState.STABLE:
                    hyp.state = HypothesisState.OBSERVING
                    hyp.updated_at = now
                    self.repository.upsert_hypothesis(hyp)
                    decayed += 1
        return decayed


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_mean_glucose(mean_mgdl: object) -> str:
    """Render the digest's mean glucose in the operator's display unit (D052).

    Storage/analytics stay mg/dL; only this user-visible sentence converts.
    """
    from hermes_cgm_agent.config import display_glucose_unit
    from hermes_cgm_agent.domain import GlucoseUnit, convert_glucose_value

    if display_glucose_unit() == "mmol/L":
        mmol = convert_glucose_value(float(mean_mgdl), GlucoseUnit.MG_DL, GlucoseUnit.MMOL_L)
        return f"平均血糖约 {round(mmol, 1)} mmol/L。"
    return f"平均血糖约 {mean_mgdl} mg/dL。"
