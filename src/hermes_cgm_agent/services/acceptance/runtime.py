"""Deterministic checks that surround the real Hermes acceptance run.

The model scenarios exercise the external Hermes process, but these checks stay
local and repeatable.  They prove that the copied profile can read L0/warm
state, that a conversation turn is queued for confirmation, that the
authoritative RAG track is quote-safe, and that scheduled reports/pushes are
idempotent.  No check writes to the canonical production database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.acceptance.models import Scenario
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import CGMMemoryProvider, SQLiteMemoryRepository
from hermes_cgm_agent.services.memory.l0_builder import L0ContextBuilder
from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.reports import ReportToolService, SQLiteReportRepository
from hermes_cgm_agent.services.safety import assert_authoritative_quotes
from hermes_cgm_agent.services.scheduling import PushSchedulerConfig, PushSchedulerService
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def validate_memory_runtime(
    db_path: Path,
    *,
    user_id: str,
    hermes_home: Path,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Check L0/warm prefetch and pending conversation-memory semantics."""

    store = SQLiteStore(db_path)
    store.initialize()
    repository = SQLiteMemoryRepository(store)
    cgm = SQLiteCGMRepository(store)
    anchor = datetime.fromisoformat(window["local_end"]).astimezone(timezone.utc)
    l0 = L0ContextBuilder(repository=cgm, config=None).build(
        user_id=user_id,
        anchor_at=anchor,
    )
    provider = CGMMemoryProvider(store, user_id=user_id)
    provider.initialize(
        session_id="acceptance-prefetch",
        user_id=user_id,
        hermes_home=str(hermes_home),
        platform="acceptance",
        anchor_at=anchor,
    )
    prefetch = provider.prefetch("当前血糖和最近的趋势", session_id="acceptance-prefetch")
    warm_present = "[CGM state summary]" in prefetch

    before_candidates = len(repository.list_candidates(user_id))
    before_l2 = len(repository.list_profile_items(user_id, active_only=False))
    before_l3 = len(repository.list_hypotheses(user_id, active_only=False))
    provider.initialize(
        session_id="acceptance-conversation",
        user_id=user_id,
        hermes_home=str(hermes_home),
        platform="acceptance",
        anchor_at=anchor,
    )
    provider.sync_turn(
        "My blood glucose is often higher after dinner; please remember this as a tentative observation.",
        "I will keep it as a pending note for your confirmation.",
        session_id="acceptance-conversation",
    )
    after_sync_candidates = len(repository.list_candidates(user_id))
    provider.on_session_end([])
    after_end_candidates = len(repository.list_candidates(user_id))
    after_l2 = len(repository.list_profile_items(user_id, active_only=False))
    after_l3 = len(repository.list_hypotheses(user_id, active_only=False))

    checks = {
        "l0_has_points": l0.window_summary.point_count > 0,
        "l0_has_events": bool(l0.key_glucose_events),
        "prefetch_non_empty": bool(prefetch.strip()),
        "warm_summary_prefetched": warm_present,
        "prefetch_l0_context": "[CGM L0 context]" in prefetch,
        "conversation_candidate_queued": after_sync_candidates > before_candidates,
        "conversation_candidate_pending": after_end_candidates >= after_sync_candidates,
        "conversation_did_not_promote_l2": after_l2 == before_l2,
        "conversation_did_not_promote_l3": after_l3 == before_l3,
    }
    return {
        "checks": checks,
        "prefetch_markers": {
            "warm": warm_present,
            "l0": "[CGM L0 context]" in prefetch,
            "personal": "[CGM user-memory recall]" in prefetch,
        },
        "l0": {
            "point_count": l0.window_summary.point_count,
            "event_count": len(l0.key_glucose_events),
            "anchor": anchor.isoformat(),
        },
        "candidate_counts": {
            "before": before_candidates,
            "after_sync": after_sync_candidates,
            "after_session_end": after_end_candidates,
        },
        "memory_counts": {
            "l2_before": before_l2,
            "l2_after": after_l2,
            "l3_before": before_l3,
            "l3_after": after_l3,
        },
    }


def validate_rag_runtime(scenarios: list[Scenario]) -> dict[str, Any]:
    """Run local retrieval and strict quote checks for the six RAG prompts."""

    service = AuthoritativeRAGService()
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        if scenario.category != "rag":
            continue
        documents = service.search(scenario.rag_query or scenario.prompt, top_k=3)
        quote_checks = []
        for document in documents:
            quote = assert_authoritative_quotes([document], document["text"], strict=True)
            quote_checks.append(bool(quote.ok))
        results.append(
            {
                "scenario_id": scenario.scenario_id,
                "documents": [
                    {
                        "doc_id": document["doc_id"],
                        "title": document["title"],
                        "verified": document["verified"],
                        "tier": document["tier"],
                    }
                    for document in documents
                ],
                "documents_present": bool(documents),
                "strict_quote_checks": quote_checks,
                "track_isolated": all("user_id" not in str(document).lower() for document in documents),
            }
        )
    checks = {
        "six_scenarios_checked": len(results) == 6,
        "documents_present": all(item["documents_present"] for item in results),
        "strict_quotes": all(all(item["strict_quote_checks"]) for item in results),
        "track_isolation": all(item["track_isolated"] for item in results),
    }
    return {"checks": checks, "scenarios": results, "kb_version": service.kb_version}


def validate_periodic_runtime(
    db_path: Path,
    *,
    user_id: str,
    timezone_name: str,
    window: dict[str, Any],
) -> dict[str, Any]:
    """Exercise daily report/push ticks over the selected simulated dates."""

    store = SQLiteStore(db_path)
    store.initialize()
    tz = ZoneInfo(timezone_name)
    scheduler = PushSchedulerService(
        store=store,
        config=PushSchedulerConfig(timezone=timezone_name),
        session_id="hermes-accept-periodic",
    )
    report_service = ReportToolService(
        cgm_repository=SQLiteCGMRepository(store),
        report_repository=SQLiteReportRepository(store),
        memory_repository=SQLiteMemoryRepository(store),
    )
    ticks: list[dict[str, Any]] = []
    reports: list[str] = []
    event_alerts: list[dict[str, Any]] = []
    duplicate_ok = True
    for day in window["window_days"]:
        local_now = datetime.fromisoformat(f"{day}T09:00:00").replace(tzinfo=tz)
        now = local_now.astimezone(timezone.utc)
        day_start = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=tz).astimezone(timezone.utc)
        day_events = SQLiteCGMRepository(store).list_glucose_events(
            DataScope(user_id=user_id, window_start=day_start, window_end=day_start + timedelta(days=1))
        )
        for event in day_events:
            event_type = str(getattr(event.event_type, "value", event.event_type))
            if event_type != "data_gap":
                event_alerts.append(
                    {"date": day, "event_id": event.event_id, "event_type": event_type}
                )
                break
        first = scheduler.push_tick(user_id=user_id, now=now)
        second = scheduler.push_tick(user_id=user_id, now=now)
        first_ids = {entry.get("push_id") for entry in first.pushed}
        second_ids = {entry.get("push_id") for entry in second.pushed}
        duplicate_ok = duplicate_ok and not (first_ids & second_ids)
        ticks.append(
            {
                "date": day,
                "first_pushed": first.to_dict()["pushed"],
                "replay_pushed": second.to_dict()["pushed"],
                "silent_consent": first.to_dict()["silent_consent"],
            }
        )
        report = report_service.generate(
            {
                "report_type": "daily",
                "user_id": user_id,
                "timezone": timezone_name,
                "anchor_at": now.isoformat(),
                "retrieve_context": True,
                "auto_ingest_memory": False,
            }
        ).report
        reports.append(report.report_id)
    with store.connect() as conn:
        push_count = int(conn.execute("SELECT COUNT(*) FROM push_events WHERE user_id = ?", (user_id,)).fetchone()[0])
        report_count = int(conn.execute("SELECT COUNT(*) FROM reports WHERE user_id = ?", (user_id,)).fetchone()[0])
    checks = {
        "three_dates_checked": len(ticks) == 3,
        "reports_generated": len(reports) == 3,
        "push_replay_idempotent": duplicate_ok,
        "event_alerts_oracle_confirmed": 0 < len(event_alerts) <= 3
        and len({(item["date"], item["event_type"]) for item in event_alerts}) == len(event_alerts),
        "no_more_than_three_periodic_reports": report_count <= 3,
    }
    return {
        "checks": checks,
        "ticks": ticks,
        "event_alerts": event_alerts,
        "report_ids": reports,
        "counts": {"reports": report_count, "push_events": push_count},
    }
