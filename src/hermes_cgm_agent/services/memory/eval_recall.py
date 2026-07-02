"""Memory efficacy evaluation (D053): does long-term memory actually help?

v1 is a *deterministic context-recall* metric, not an LLM-judged answer-quality
score — so it is zero-cost and CI-gateable (Principle V). For each query we run
``CGMMemoryProvider.prefetch`` against two stores: one seeded with a fixture
corpus (L1 episodes, L2 profile beliefs, L3 hypotheses, a warm summary) and one
empty. The score is the fraction of a query's ``expected_terms`` that appear in
the injected context. The with-memory vs without-memory gap is the evidence that
the memory system — not the prompt — supplies the personalized facts.

LLM-graded answer quality is a known gap left for a later revision.
"""

from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from hermes_cgm_agent.domain import (
    EvidenceRef,
    HypothesisState,
    L1Episode,
    L2ProfileItem,
    L3Hypothesis,
    MemorySummary,
)
from hermes_cgm_agent.domain.cgm import utc_now
from hermes_cgm_agent.services.memory import (
    CGMMemoryProvider,
    SQLiteMemoryRepository,
    new_id,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def seed_fixture(store: SQLiteStore, *, user_id: str, fixture_path: Path) -> int:
    """Seed a deterministic personal-memory corpus. Timestamps are relative to
    now (never fixed dates — those rot, cf. Phase 0)."""
    repo = SQLiteMemoryRepository(store)
    now = utc_now()
    count = 0
    for i, row in enumerate(load_jsonl(fixture_path)):
        kind = row["kind"]
        if kind == "l1":
            repo.create_episode(
                L1Episode(
                    episode_id=new_id(),
                    user_id=user_id,
                    occurred_at=now - timedelta(days=int(row.get("days_ago", 1))),
                    episode_type=row.get("episode_type", "observation"),
                    summary=row["summary"],
                    evidence_refs=[EvidenceRef(kind="event", ref_id=f"seed-{i}")],
                    confidence=float(row.get("confidence", 0.8)),
                )
            )
        elif kind == "l2":
            repo.upsert_profile_item(
                L2ProfileItem(
                    item_id=new_id(),
                    user_id=user_id,
                    key=row["key"],
                    value={"summary": row["summary"]},
                    confidence=float(row.get("confidence", 0.8)),
                )
            )
        elif kind == "l3":
            repo.upsert_hypothesis(
                L3Hypothesis(
                    hypothesis_id=new_id(),
                    user_id=user_id,
                    statement=row["statement"],
                    state=HypothesisState(row.get("state", "observing")),
                    evidence_count=int(row.get("evidence_count", 2)),
                )
            )
        elif kind == "warm":
            repo.create_summary(
                MemorySummary(
                    summary_id=new_id(),
                    user_id=user_id,
                    period=row.get("period", "weekly"),
                    window_start=now - timedelta(days=int(row.get("window_days", 7))),
                    window_end=now,
                    content=row["content"],
                )
            )
        else:  # pragma: no cover - guard against malformed fixture rows
            raise ValueError(f"unknown fixture kind: {kind!r}")
        count += 1
    return count


def _recall(context: str, expected_terms: list[str]) -> float:
    if not expected_terms:
        return 0.0
    haystack = context.lower()
    hits = sum(1 for term in expected_terms if str(term).lower() in haystack)
    return hits / len(expected_terms)


def evaluate_memory_recall(
    *,
    queries_path: Path,
    fixture_path: Path,
    user_id: str = "eval-user",
    report_path: Path | None = None,
) -> dict[str, Any]:
    queries = load_jsonl(queries_path)

    with tempfile.TemporaryDirectory() as tmp:
        seeded = SQLiteStore(Path(tmp) / "seeded.db")
        seeded.initialize()
        seed_count = seed_fixture(seeded, user_id=user_id, fixture_path=fixture_path)
        empty = SQLiteStore(Path(tmp) / "empty.db")
        empty.initialize()

        with_provider = CGMMemoryProvider(seeded, user_id=user_id)
        with_provider.initialize(session_id="eval", user_id=user_id)
        without_provider = CGMMemoryProvider(empty, user_id=user_id)
        without_provider.initialize(session_id="eval", user_id=user_id)

        per_query: list[dict[str, Any]] = []
        for row in queries:
            query = str(row["query"])
            terms = list(row.get("expected_terms") or [])
            ctx_with = with_provider.prefetch(query)
            ctx_without = without_provider.prefetch(query)
            r_with = _recall(ctx_with, terms)
            r_without = _recall(ctx_without, terms)
            per_query.append(
                {
                    "query": query,
                    "layer": row.get("expected_layer"),
                    "expected_terms": terms,
                    "recall_with": round(r_with, 4),
                    "recall_without": round(r_without, 4),
                }
            )

    total = len(per_query)
    mean_with = round(sum(q["recall_with"] for q in per_query) / total, 4) if total else 0.0
    mean_without = (
        round(sum(q["recall_without"] for q in per_query) / total, 4) if total else 0.0
    )
    report = {
        "total": total,
        "seed_count": seed_count,
        "mean_recall_with": mean_with,
        "mean_recall_without": mean_without,
        "delta": round(mean_with - mean_without, 4),
        "per_query": per_query,
    }
    if report_path is not None:
        report_path.write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Memory Efficacy Report (D053)",
        "",
        f"- Queries: **{report['total']}** · Seed corpus rows: **{report['seed_count']}**",
        f"- Mean recall **with** memory: **{report['mean_recall_with']}**",
        f"- Mean recall **without** memory (empty store): **{report['mean_recall_without']}**",
        f"- **Delta (evidence that memory helps): {report['delta']}**",
        "",
        "Method: deterministic context recall — fraction of each query's "
        "`expected_terms` present in `CGMMemoryProvider.prefetch(query)`, seeded "
        "store vs empty store. No LLM (v1); answer-quality grading is a known gap.",
        "",
        "| Query | Layer | recall (with) | recall (without) |",
        "|---|---|---|---|",
    ]
    for q in report["per_query"]:
        lines.append(
            f"| {q['query']} | {q['layer'] or ''} | {q['recall_with']} | {q['recall_without']} |"
        )
    return "\n".join(lines) + "\n"
