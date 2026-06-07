from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cgm_agent.services.arguments import optional_bool, optional_int, require_bool
from hermes_cgm_agent.services.rag.authoritative import (
    AuthoritativeRAGService,
    normalize_population,
)
from hermes_cgm_agent.services.safety import assert_authoritative_quotes, query_number_coverage


@dataclass(frozen=True)
class AuthoritativeRAGToolResult:
    query: str
    documents: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    kb_version: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class VerifyQuotesToolResult:
    ok: bool
    mode: str
    violations: list[str]
    checked_documents: int


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
        if population is not None:
            payload["population_filter"] = normalize_population(population)
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

    def verify_quotes(self, arguments: dict[str, Any]) -> VerifyQuotesToolResult:
        generated_text = str(arguments["generated_text"])
        if not generated_text.strip():
            raise ValueError("generated_text must be a non-empty string")
        strict = require_bool(arguments.get("strict", False), "strict")
        documents = arguments.get("documents")
        if documents is not None and not isinstance(documents, list):
            raise ValueError("documents must be a list when provided")
        if not documents:
            query = arguments.get("query")
            if not (query and str(query).strip()):
                raise ValueError(
                    "provide either documents or a non-empty query to verify against"
                )
            top_k = optional_int(
                arguments.get("top_k"),
                "top_k",
                default=5,
                minimum=1,
                maximum=20,
            )
            documents = self.rag_service.search(str(query).strip(), top_k=top_k)
        result = assert_authoritative_quotes(documents, generated_text, strict=strict)
        return VerifyQuotesToolResult(
            ok=result.ok,
            mode=result.mode,
            violations=result.violations,
            checked_documents=len(documents),
        )
