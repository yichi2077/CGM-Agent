from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.services.arguments import optional_int
from hermes_cgm_agent.services.rag.authoritative import AuthoritativeRAGService
from hermes_cgm_agent.services.safety import query_number_coverage


@dataclass(frozen=True)
class AuthoritativeRAGToolResult:
    query: str
    documents: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    kb_version: str
    payload: dict[str, Any]


class AuthoritativeRAGToolService:
    """Tool-facing orchestration for authoritative KB retrieval."""

    def __init__(self, rag_service: AuthoritativeRAGService | None = None) -> None:
        self.rag_service = rag_service or AuthoritativeRAGService()

    def search(self, arguments: dict[str, Any]) -> AuthoritativeRAGToolResult:
        query = str(arguments["query"]).strip()
        if not query:
            raise ValueError("query must be a non-empty string")
        top_k = optional_int(
            arguments.get("top_k"),
            "top_k",
            default=3,
            minimum=1,
            maximum=20,
        )
        population = arguments.get("population")
        if population is not None:
            population = str(population).strip() or None
        documents = self.rag_service.search(query, top_k=top_k, population=population)
        evidence_refs = [doc["evidence_ref"] for doc in documents]
        payload: dict[str, Any] = {
            "documents": documents,
            "kb_version": self.rag_service.kb_version,
            "quote_instruction": "verbatim_only",
        }
        # NOTE: this is a retrieval-coverage hint (which numbers in the user's
        # query are absent from retrieved evidence), NOT anti-hallucination.
        coverage = query_number_coverage(documents, query)
        if coverage.violations:
            payload["query_number_coverage"] = {
                "mode": coverage.mode,
                "uncovered": coverage.violations,
            }
        return AuthoritativeRAGToolResult(
            query=query,
            documents=documents,
            evidence_refs=evidence_refs,
            kb_version=self.rag_service.kb_version,
            payload=payload,
        )
