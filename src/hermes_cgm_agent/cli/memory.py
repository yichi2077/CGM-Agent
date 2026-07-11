from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from hermes_cgm_agent.domain import DataScope, GlucoseEvent, L1Episode
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.analytics import CGMAnalyticsService, GlucoseEventDetector
from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import (
    CGMImporter,
    CGMNormalizer,
    NormalizationConfig,
    SQLiteCGMRepository,
)
from hermes_cgm_agent.services.memory import (
    ConsolidationService,
    L0ContextBuilder,
    MemoryContextAssembler,
    SQLiteMemoryRepository,
)
from hermes_cgm_agent.services.memory.derive import episodes_from_detected_events
from hermes_cgm_agent.services.memory.user_md_sync import render_l2_user_md_block
from hermes_cgm_agent.storage.sqlite import SQLiteStore

from hermes_cgm_agent.cli.utils import _parse_iso_datetime, _period_to_window_label


def _memory_synthesize(
    *,
    db_path: Path,
    user_id: str,
    window_start: str,
    window_end: str,
    period: str,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    memory_repository = SQLiteMemoryRepository(store)
    scope = DataScope(
        user_id=user_id,
        window_start=_parse_iso_datetime(window_start),
        window_end=_parse_iso_datetime(window_end),
    )
    aggregate = CGMAnalyticsService().compute_aggregate(
        points=repository.list_glucose_points(scope),
        scope=scope,
        window_label=_period_to_window_label(period),
    )
    summary = ConsolidationService(repository=memory_repository).synthesize_state(
        user_id=user_id,
        window_start=scope.window_start,
        window_end=scope.window_end,
        period=period,
        metrics_summary={
            "tir_pct": aggregate.tir,
            "mean_mgdl": aggregate.mbg,
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "summary_id": summary.summary_id,
                "user_id": summary.user_id,
                "period": summary.period,
                "window_start": summary.window_start.isoformat(),
                "window_end": summary.window_end.isoformat(),
                "content": summary.content,
                "metrics": summary.metrics,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _context_build(
    *,
    db_path: Path,
    user_id: str,
    anchor_at: str | None,
    source: str | None,
) -> int:
    store = SQLiteStore(db_path)
    store.initialize()
    context = L0ContextBuilder(
        repository=SQLiteCGMRepository(store),
    ).build(
        user_id=user_id,
        anchor_at=_parse_iso_datetime(anchor_at) if anchor_at else None,
        source=source,
    )
    print(json.dumps(context.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return 0


def _default_demo_csv() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "cgm_test_dataset"
        / "cgm_3x14.csv"
    )


def _episodes_from_detected_events(
    events: list[GlucoseEvent], *, now: datetime, timezone_name: str | None = None
) -> list[L1Episode]:
    """Derive L1 episodes from DETECTED glucose events (P1 demo seeding).

    Detected hypo/hyper/overnight-low events are deterministic FACTS about the
    data (not agent inferences), each carrying a real per-day timestamp — so they
    are a faithful, multi-day data-driven source for the memory chain. occurred_at
    is the event's real start time (NOT processing time), so consolidation groups
    them across the actual calendar days the patterns occurred. This is CLI-local
    demo orchestration; it does not change the production confirmation-gated
    memory path (D026).
    """
    return episodes_from_detected_events(events, now=now, timezone_name=timezone_name)


def _seed_demo(
    *,
    db_path: Path,
    csv_path: Path,
    user_id: str,
    timezone_name: str,
    query: str,
) -> int:
    if not csv_path.exists():
        print(
            json.dumps(
                {"status": "error", "message": f"CSV not found: {csv_path}"},
                ensure_ascii=False,
            )
        )
        return 1

    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    memory_repository = SQLiteMemoryRepository(store)

    # 1. import + normalize the CGM CSV into storage
    batch = CGMImporter().import_csv(csv_path)
    normalized = CGMNormalizer().normalize_batch(
        batch,
        NormalizationConfig(
            user_id=user_id,
            source=f"seed-demo:{csv_path.stem}",
            default_timezone=timezone_name,
        ),
    )
    repository.create_import_batch(
        batch.model_copy(update={"issues": [*batch.issues, *normalized.issues]})
    )
    inserted = 0
    duplicate = 0
    for point in normalized.points:
        try:
            repository.create_glucose_point(point)
            inserted += 1
        except sqlite3.IntegrityError:
            duplicate += 1

    if not normalized.points:
        print(json.dumps({"status": "error", "message": "no valid points imported"}))
        return 1

    window_start = min(point.timestamp for point in normalized.points)
    window_end = max(point.timestamp for point in normalized.points) + timedelta(minutes=5)
    scope = DataScope(user_id=user_id, window_start=window_start, window_end=window_end)
    stored_points = repository.list_glucose_points(scope)

    # 2. analytics over the full window
    aggregate = CGMAnalyticsService().compute_aggregate(
        points=stored_points, scope=scope, window_label="14d"
    )

    # 3. detect events -> L1 episodes dated by their real occurrence (data-driven memory)
    now = utc_now()
    events = GlucoseEventDetector().detect(points=stored_points, scope=scope)
    episodes = _episodes_from_detected_events(events, now=now, timezone_name=timezone_name)
    episode_inserted = 0
    for episode in episodes:
        try:
            memory_repository.create_episode(episode)
            episode_inserted += 1
        except sqlite3.IntegrityError:
            pass  # idempotent re-seed

    # 4. consolidate L1 -> L2 beliefs + L3 hypotheses (groups by distinct local day)
    consolidation = ConsolidationService(
        repository=memory_repository, audit_service=AuditService(store)
    )
    consolidation_report = consolidation.consolidate(user_id, now=now)

    # 5. synthesize a warm state summary (the "dreaming" digest used in prefetch)
    summary = consolidation.synthesize_state(
        user_id=user_id,
        window_start=window_start,
        window_end=window_end,
        period="weekly",
        metrics_summary={"tir_pct": aggregate.tir, "mean_mgdl": aggregate.mbg},
        now=now,
    )

    # 6. recall: assemble the personal-memory context for a query (prefetch core)
    recall = MemoryContextAssembler(repository=memory_repository).build_memory_context(
        user_id=user_id, query=query, top_k=5
    )
    profile_items = memory_repository.list_profile_items(user_id)

    # 7. show the USER.md L2 projection that would sync (no disk write here)
    user_md_preview = render_l2_user_md_block(profile_items)

    payload = {
        "status": "ok",
        "database_path": str(db_path),
        "data_chain": {
            "csv": str(csv_path),
            "points_inserted": inserted,
            "points_duplicate": duplicate,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "tir_pct": aggregate.tir,
            "mean_mgdl": aggregate.mbg,
            "detected_events": len(events),
        },
        "memory_chain": {
            "l1_episodes_created": episode_inserted,
            "l1_episode_total": len(memory_repository.list_episodes(user_id)),
            "l2_profiles_updated": consolidation_report.profiles_updated,
            "l3_hypotheses_updated": consolidation_report.hypotheses_updated,
            "l2_profile_total": len(profile_items),
            "l3_hypothesis_total": len(memory_repository.list_hypotheses(user_id)),
            "warm_summary_id": summary.summary_id,
            "warm_summary": summary.content,
        },
        "recall": {
            "query": query,
            "item_count": len(recall.items),
            "items": [
                {"layer": item["layer"], "summary": item["summary"]}
                for item in recall.items
            ],
            "missing_reason": recall.missing_reason,
        },
        "user_md_l2_preview": user_md_preview,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0
