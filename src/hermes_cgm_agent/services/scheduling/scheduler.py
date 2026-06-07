"""Tiered push scheduling + silent-consent (P2 / PRD §1.4, §2.4).

The PRD interaction paradigm is "日 30 字 → 周模式 → 月报告，静默即认可". This is a
*stateless trigger + persisted state* design (the 2026 best practice for proactive
agents): the project owns the **policy** (which tier is due), the **content**
(reusing ConsolidationService.synthesize_state) and the **state** (push_events +
silence window); the *timing* is driven externally by Hermes/cron calling
``push_tick``. There is NO resident scheduler process here (open-ended interaction
and scheduling cadence belong to Hermes — AGENTS.md).

Idempotency: each (user_id, tier, period_key) is pushed at most once — the
push_events UNIQUE constraint is the backstop, and ``decide_due_tiers`` skips
already-pushed periods.

Silent-consent (静默即认可) is deliberately narrow and safe: a *behavioral*
hypothesis in the low-commitment ``candidate`` state, left unobjected for the
silence window, advances to ``observing`` ("keep watching"). It NEVER auto-marks
``stable`` (that needs evidence via consolidation), NEVER touches archived
hypotheses, and NEVER auto-accepts safety/medical content or D026-gated memory
candidates. Every advance is audited and reversible (memory.correct).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import DataScope, HypothesisState
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import ConsolidationService, SQLiteMemoryRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore

TIERS = ("daily", "weekly", "monthly")
_TIER_SPAN_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
_TIER_WINDOW_LABEL = {"daily": "day", "weekly": "week", "monthly": "month"}


@dataclass(frozen=True)
class PushSchedulerConfig:
    timezone: str = "Asia/Shanghai"
    daily_hour: int = 9          # push the daily digest at/after 09:00 local
    weekly_weekday: int = 0      # Monday (Python weekday(): Mon=0)
    monthly_day: int = 1         # 1st of the month
    silence_days: int = 3        # no objection within N days -> implicit agreement


@dataclass(frozen=True)
class PushTickResult:
    user_id: str
    now: str
    pushed: list[dict[str, Any]] = field(default_factory=list)
    silent_consent: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "user_id": self.user_id,
            "now": self.now,
            "pushed": list(self.pushed),
            "silent_consent": list(self.silent_consent),
        }


class PushSchedulerService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        config: PushSchedulerConfig | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.store = store
        self.config = config or PushSchedulerConfig()
        self.cgm = SQLiteCGMRepository(store)
        self.memory = SQLiteMemoryRepository(store)
        self.analytics = CGMAnalyticsService()
        self.consolidation = ConsolidationService(repository=self.memory)
        self.audit_service = audit_service

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    # ── period keys ─────────────────────────────────────────────────────────
    def period_key(self, tier: str, when: datetime) -> str:
        local = when.astimezone(self._tz)
        if tier == "daily":
            return local.strftime("%Y-%m-%d")
        if tier == "weekly":
            iso = local.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        return local.strftime("%Y-%m")  # monthly

    # ── decision ────────────────────────────────────────────────────────────
    def decide_due_tiers(self, now: datetime, user_id: str) -> list[str]:
        local = now.astimezone(self._tz)
        due: list[str] = []
        if local.hour >= self.config.daily_hour:
            if not self._already_pushed(user_id, "daily", self.period_key("daily", now)):
                due.append("daily")
            if (
                local.weekday() == self.config.weekly_weekday
                and not self._already_pushed(user_id, "weekly", self.period_key("weekly", now))
            ):
                due.append("weekly")
            if (
                local.day == self.config.monthly_day
                and not self._already_pushed(user_id, "monthly", self.period_key("monthly", now))
            ):
                due.append("monthly")
        return due

    # ── tick ────────────────────────────────────────────────────────────────
    def push_tick(self, *, user_id: str, now: datetime | None = None) -> PushTickResult:
        now = now or utc_now()
        consent = self.apply_silent_consent(user_id=user_id, now=now)
        pushed: list[dict[str, Any]] = []
        for tier in self.decide_due_tiers(now, user_id):
            emitted = self._emit(tier, user_id=user_id, now=now)
            if emitted is not None:
                pushed.append(emitted)
        if self.audit_service is not None and (pushed or consent):
            self.audit_service.log(
                session_id="push-scheduler",
                event_type="push_tick",
                payload={
                    "user_id": user_id,
                    "status": "ok",
                    "pushed_tiers": [p["tier"] for p in pushed],
                    "silent_consent_count": len(consent),
                },
            )
        return PushTickResult(
            user_id=user_id, now=now.isoformat(), pushed=pushed, silent_consent=consent
        )

    def _emit(self, tier: str, *, user_id: str, now: datetime) -> dict[str, Any] | None:
        window_start = now - timedelta(days=_TIER_SPAN_DAYS[tier])
        scope = DataScope(user_id=user_id, window_start=window_start, window_end=now)
        aggregate = self.analytics.compute_aggregate(
            points=self.cgm.list_glucose_points(scope),
            scope=scope,
            window_label=_TIER_WINDOW_LABEL[tier],
        )
        summary = self.consolidation.synthesize_state(
            user_id=user_id,
            window_start=window_start,
            window_end=now,
            period=tier,
            metrics_summary={"tir_pct": aggregate.tir, "mean_mgdl": aggregate.mbg},
            now=now,
        )
        period = self.period_key(tier, now)
        push_id = self._record_push(
            user_id=user_id, tier=tier, period_key=period, summary_id=summary.summary_id, now=now
        )
        if push_id is None:
            return None  # already pushed this period (idempotent)
        return {
            "tier": tier,
            "period_key": period,
            "push_id": push_id,
            "summary_id": summary.summary_id,
            "content": summary.content,
        }

    # ── silent consent ────────────────────────────────────────────────────────
    def apply_silent_consent(
        self, *, user_id: str, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Advance unobjected ``candidate`` behavioral hypotheses to ``observing``.

        Safe by construction: only candidate -> observing (a watch-state, no action
        taken); never stable/archived; fully reversible via memory.correct.
        """
        now = now or utc_now()
        threshold = now - timedelta(days=self.config.silence_days)
        advanced: list[dict[str, Any]] = []
        for hyp in self.memory.list_hypotheses(user_id):
            if hyp.state != HypothesisState.CANDIDATE:
                continue
            last = hyp.last_checked or hyp.created_at
            if last is not None and last > threshold:
                continue  # still inside the silence window
            hyp.state = HypothesisState.OBSERVING
            hyp.last_checked = now
            hyp.updated_at = now
            self.memory.upsert_hypothesis(hyp)
            advanced.append(
                {"hypothesis_id": hyp.hypothesis_id, "statement": hyp.statement, "to": "observing"}
            )
            if self.audit_service is not None:
                self.audit_service.log(
                    session_id="push-scheduler",
                    event_type="silent_consent",
                    payload={
                        "user_id": user_id,
                        "status": "ok",
                        "hypothesis_id": hyp.hypothesis_id,
                        "from": "candidate",
                        "to": "observing",
                        "silence_days": self.config.silence_days,
                    },
                )
        return advanced

    # ── push_events persistence ───────────────────────────────────────────────
    def _already_pushed(self, user_id: str, tier: str, period_key: str) -> bool:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM push_events WHERE user_id = ? AND tier = ? AND period_key = ?",
                (user_id, tier, period_key),
            ).fetchone()
        return row is not None

    def _record_push(
        self, *, user_id: str, tier: str, period_key: str, summary_id: str, now: datetime
    ) -> str | None:
        push_id = uuid.uuid4().hex
        try:
            with self.store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO push_events
                        (push_id, user_id, tier, period_key, summary_id, delivery_id, pushed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (push_id, user_id, tier, period_key, summary_id, None, now.isoformat()),
                )
        except sqlite3.IntegrityError:
            # UNIQUE(user_id, tier, period_key) violated -> already pushed.
            return None
        return push_id
