"""Accelerated historical-data replay (D051).

``ReplayService.run`` imports a CGM dataset, optionally shifts its timestamps so
it ends around "now", inserts every point once (UNIQUE(user_id,timestamp,source)
makes reruns idempotent), then walks a simulated clock day-by-day. Each simulated
day it triggers ``scheduling.push_tick`` **through the real ToolExecutor** (audit
parity with production) and, when ``deliver`` is set, forwards each emitted push
to ``delivery.send`` — which back-writes ``push_events.delivery_id`` (D052),
closing the loop end to end.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore

_MAX_PUSH_TEXT = 100  # companion push text is contractually <=100 chars (FR-005)


@dataclass(frozen=True)
class ReplayConfig:
    dataset: Path
    user_id: str
    days: int | None = None            # trim to the last N days of the dataset
    speed: str = "instant"             # "instant" | "daily-step"
    step_seconds: float = 2.0          # inter-day pause for a live "daily-step" demo
    deliver: bool = False              # bridge each push to delivery.send (local_file)
    align_end_to_now: bool = True      # shift timestamps so the dataset ends ~yesterday
    push_hour: int = 9                 # simulated tick hour (>= scheduler daily_hour)
    timezone: str = "Asia/Shanghai"    # must match the scheduler's tz for day gating
    replace: bool = False              # importer dedupe override


@dataclass
class ReplayReport:
    user_id: str
    days_simulated: int = 0
    points_imported: int = 0
    ticks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_pushes(self) -> int:
        return sum(len(t["pushed"]) for t in self.ticks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "user_id": self.user_id,
            "days_simulated": self.days_simulated,
            "points_imported": self.points_imported,
            "total_pushes": self.total_pushes,
            "ticks": self.ticks,
        }


class ReplayService:
    def __init__(
        self,
        *,
        store: SQLiteStore,
        executor: ToolExecutor | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._repository = SQLiteCGMRepository(store)
        self._executor = executor or ToolExecutor(
            repository=self._repository,
            audit_service=AuditService(store),
        )
        self._sleep = sleep

    def run(self, config: ReplayConfig) -> ReplayReport:
        report = ReplayReport(user_id=config.user_id)
        points = self._load_points(config)
        if not points:
            return report

        # Trim to the last N days of the dataset (by native timestamp).
        max_ts = max(p.timestamp for p in points)
        if config.days is not None:
            cutoff = max_ts - timedelta(days=config.days)
            points = [p for p in points if p.timestamp >= cutoff]
            max_ts = max(p.timestamp for p in points)

        # Optionally shift the whole series so it ends ~yesterday, so a post-replay
        # real-time chat/prefetch (which anchors L0 to real now) still sees it.
        if config.align_end_to_now:
            target_end = datetime.now(timezone.utc) - timedelta(days=1)
            offset = target_end - max_ts
            points = [p.model_copy(update={"timestamp": p.timestamp + offset}) for p in points]

        min_ts = min(p.timestamp for p in points)
        max_ts = max(p.timestamp for p in points)

        # Insert every point up front. Each tick bounds its window by
        # window_end=sim_now, so points dated after a given tick stay invisible
        # to it — no need to feed points incrementally.
        for point in points:
            try:
                self._repository.create_glucose_point(point, replace=config.replace)
                report.points_imported += 1
            except sqlite3.IntegrityError:
                pass  # idempotent rerun

        tz = ZoneInfo(config.timezone)
        day = min_ts.astimezone(tz).date()
        end_day = max_ts.astimezone(tz).date()
        while day <= end_day:
            sim_now = datetime(
                day.year, day.month, day.day, config.push_hour, 30, tzinfo=tz
            )
            report.ticks.append(self._tick(config, sim_now))
            report.days_simulated += 1
            if config.speed == "daily-step":
                self._sleep(config.step_seconds)
            day += timedelta(days=1)
        return report

    def _load_points(self, config: ReplayConfig) -> list[Any]:
        batch = CGMImporter().import_csv(config.dataset)
        normalized = CGMNormalizer().normalize_batch(
            batch,
            NormalizationConfig(
                user_id=config.user_id,
                source=f"replay:{config.dataset.stem}",
                default_timezone=config.timezone,
            ),
        )
        return list(normalized.points)

    def _tick(self, config: ReplayConfig, sim_now: datetime) -> dict[str, Any]:
        body = self._executor.execute(
            tool_name="scheduling.push_tick",
            arguments={"user_id": config.user_id, "now": sim_now.isoformat()},
            session_id="replay",
        ).to_dict()
        pushed = list(body.get("pushed", []))
        delivery_ids: list[str] = []
        if config.deliver:
            for item in pushed:
                sent = self._executor.execute(
                    tool_name="delivery.send",
                    arguments={
                        "channel": "local_file",
                        "user_id": config.user_id,
                        "payload_ref": item["push_id"],
                        "tier": item["tier"],
                        "period_key": item["period_key"],
                    },
                    session_id="replay",
                ).to_dict()
                delivery_ids.append(sent.get("delivery_id"))
        return {
            "sim_now": sim_now.isoformat(),
            "pushed": [
                {
                    "tier": item["tier"],
                    "period_key": item["period_key"],
                    "push_id": item["push_id"],
                    "content": item.get("content", ""),
                }
                for item in pushed
            ],
            "silent_consent_count": len(body.get("silent_consent", [])),
            "delivery_ids": delivery_ids,
        }
