from __future__ import annotations

import json
from pathlib import Path

from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.rag.authoritative import load_knowledge_base


def load_queries(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def evaluate_hit3(*, queries_path: Path, kb_path: Path | None = None) -> dict:
    service = (
        AuthoritativeRAGService(knowledge_base=load_knowledge_base(kb_path))
        if kb_path is not None
        else AuthoritativeRAGService()
    )

    queries = load_queries(queries_path)
    hits = 0
    misses: list[dict] = []
    for row in queries:
        query = str(row["query"])
        expected = set(row.get("expected_any") or [])
        # A1: exercise the population filter end-to-end when a query declares one.
        population = row.get("population")
        results = service.search(query, top_k=3, population=population)
        found = {doc["doc_id"] for doc in results}
        matched = bool(expected & found)
        if matched:
            hits += 1
        else:
            misses.append(
                {
                    "query": query,
                    "expected_any": sorted(expected),
                    "found": sorted(found),
                }
            )
    total = len(queries)
    return {
        "total": total,
        "hits": hits,
        "hit_at_3": round(hits / total, 4) if total else 0.0,
        "kb_version": service.kb_version,
        "misses": misses,
    }
