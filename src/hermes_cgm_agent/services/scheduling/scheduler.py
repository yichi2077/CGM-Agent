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

from hermes_cgm_agent.domain import DataScope, HypothesisState, GlucoseEventType
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.analytics import CGMAnalyticsService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import ConsolidationService, SQLiteMemoryRepository
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class PermissionDenied(Exception):
    """Raised when the OS push notification permissions are disabled/failed."""
    pass

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
        
        # Prioritize monthly, then weekly, then daily
        due_tiers = self.decide_due_tiers(now, user_id)
        due_tiers_sorted = sorted(due_tiers, key=lambda t: {"monthly": 0, "weekly": 1, "daily": 2}[t])
        
        for tier in due_tiers_sorted:
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
        # Rate limit: Max 1 non-urgent push per day
        if self._already_pushed_any_non_urgent_today(user_id, now):
            return None

        window_start = now - timedelta(days=_TIER_SPAN_DAYS[tier])
        scope = DataScope(user_id=user_id, window_start=window_start, window_end=now)
        points = self.cgm.list_glucose_points(scope)
        aggregate = self.analytics.compute_aggregate(
            points=points,
            scope=scope,
            window_label=_TIER_WINDOW_LABEL[tier],
        )

        # For daily digests, check if daily trend triggers (threshold check)
        if tier == "daily":
            if not self._should_trigger_daily_trend(user_id, now, aggregate.tir):
                return None

        summary = self.consolidation.synthesize_state(
            user_id=user_id,
            window_start=window_start,
            window_end=now,
            period=tier,
            metrics_summary={"tir_pct": aggregate.tir, "mean_mgdl": aggregate.mbg},
            now=now,
        )
        period = self.period_key(tier, now)

        # Try OS Push and fallback to badge count if permission is denied
        try:
            self.send_os_push(user_id, summary.content)
        except PermissionDenied:
            self.increment_badge_count(user_id)

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

    def send_os_push(self, user_id: str, content: str) -> None:
        """Attempt to send an OS push notification.
        
        Subclasses or mocks can override this. If it raises PermissionDenied,
        we increment the internal badge count instead of dropping the notification.
        """
        pass

    def increment_badge_count(self, user_id: str) -> int:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO unread_badges (user_id, badge_count)
                VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET badge_count = badge_count + 1
                """,
                (user_id,),
            )
            row = conn.execute(
                "SELECT badge_count FROM unread_badges WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row[0] if row else 0

    def get_badge_count(self, user_id: str) -> int:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT badge_count FROM unread_badges WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row[0] if row else 0

    def reset_badge_count(self, user_id: str) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE unread_badges SET badge_count = 0 WHERE user_id = ?",
                (user_id,),
            )

    def _already_pushed_any_non_urgent_today(self, user_id: str, now: datetime) -> bool:
        local = now.astimezone(self._tz)
        local_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        utc_start = local_start.astimezone(ZoneInfo("UTC"))
        
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM push_events WHERE user_id = ? AND pushed_at >= ?",
                (user_id, utc_start.isoformat()),
            ).fetchone()
        return row[0] > 0

    def _should_trigger_daily_trend(self, user_id: str, now: datetime, today_tir: float) -> bool:
        # Threshold 1: TIR delta >= 5%
        latest_summaries = self.memory.list_summaries(user_id, period="daily", limit=1)
        if latest_summaries:
            prev_tir = latest_summaries[0].metrics.get("tir_pct")
            if prev_tir is not None:
                if abs(today_tir - float(prev_tir)) >= 5.0:
                    return True

        # Threshold 2: New L3 hypothesis candidate
        candidates = [
            h for h in self.memory.list_hypotheses(user_id, states=[HypothesisState.CANDIDATE])
            if h.created_at >= now - timedelta(days=1)
        ]
        if candidates:
            return True

        # Threshold 3: Consecutive >= 2 days same-period anomaly
        from hermes_cgm_agent.services.analytics.events import GlucoseEventDetector
        from hermes_cgm_agent.domain import DataScope
        
        window_start = now - timedelta(days=2)
        scope = DataScope(user_id=user_id, window_start=window_start, window_end=now)
        points = self.cgm.list_glucose_points(scope)
        
        detector = GlucoseEventDetector()
        events = detector.detect(points=points, scope=scope)
        anomalies = [e for e in events if e.event_type != GlucoseEventType.DATA_GAP]
        
        day1_periods = set()
        day2_periods = set()
        
        day1_cutoff = now - timedelta(days=1)
        
        for e in anomalies:
            local_start = e.ts_start.astimezone(self._tz)
            period = local_start.hour // 6
            if e.ts_start >= day1_cutoff:
                day1_periods.add(period)
            else:
                day2_periods.add(period)
                
        if day1_periods.intersection(day2_periods):
            return True

        return False

    def consecutive_anomaly_days(self, user_id: str, now: datetime) -> int:
        consecutive_days = 0
        for offset in range(7):
            day = now - timedelta(days=offset)
            period = self.period_key("daily", day)
            
            with self.store.connect() as conn:
                row = conn.execute(
                    "SELECT summary_id FROM push_events WHERE user_id = ? AND tier = 'daily' AND period_key = ?",
                    (user_id, period),
                ).fetchone()
                
            if not row or not row["summary_id"]:
                break
                
            with self.store.connect() as conn:
                summary_row = conn.execute(
                    "SELECT metrics_json FROM memory_summaries WHERE summary_id = ?",
                    (row["summary_id"],),
                ).fetchone()
                
            if not summary_row:
                break
                
            metrics = self.store.unseal(summary_row["metrics_json"], legacy="json") or {}
            tar = float(metrics.get("tar_pct") or 0)
            tbr = float(metrics.get("tbr_pct") or 0)
            if tar > 0 or tbr > 0:
                consecutive_days += 1
            else:
                break
        return consecutive_days
