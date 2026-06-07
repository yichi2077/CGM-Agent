from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import os

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.rag import AuthoritativeRAGService, load_knowledge_base
from hermes_cgm_agent.services.rag.authoritative import ClaimCard, KnowledgeBase
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def _kb_with_one_curated_and_noisy_autos() -> KnowledgeBase:
    """A curated card plus several lexically-dominant auto cards on the same topic.

    The auto cards repeat the query terms so plain BM25 would rank them above the
    curated card — the trusted-first guard must still surface the curated card.
    """
    curated = ClaimCard(
        card_id="curated-tir-adults",
        title="TIR adults",
        claim_zh="成人目标范围内时间应高于70%",
        claim_en="For most adults the time in range target is above 70 percent.",
        tags=["TIR", "targets"],
        synonyms=["time in range"],
        source={"citation": "DC 2019", "page": 16},
        verified=False,
        tier="curated",
    )
    autos = [
        ClaimCard(
            card_id=f"auto-noise-{i}",
            title=f"auto {i}",
            claim_zh="",
            claim_en=(
                "time in range time in range target adults time in range "
                f"fragment {i} time in range adults target"
            ),
            tags=["auto-sentence"],
            source={"citation": "noise", "page": i},
            verified=False,
            tier="auto",
        )
        for i in range(1, 8)
    ]
    return KnowledgeBase(kb_version="kb-guard-test", cards=[curated, *autos])


class AuthoritativeRAGTests(unittest.TestCase):
    def test_knowledge_base_loads_with_version(self) -> None:
        kb = load_knowledge_base()
        self.assertTrue(kb.kb_version)
        self.assertTrue(kb.cards)

    def test_env_var_override_loads_custom_kb(self) -> None:
        # C7: an operator can point the loader at an explicit KB file, and the
        # default load must resolve from packaged data (not a repo-root guess).
        import os

        custom = {
            "kb_version": "custom-kb-9",
            "cards": [
                {
                    "card_id": "x",
                    "title": "Custom",
                    "claim_zh": "自定义条目",
                    "claim_en": "custom entry",
                    "tags": [],
                    "synonyms": ["bespoke"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_file = Path(temp_dir) / "kb.json"
            kb_file.write_text(json.dumps(custom), encoding="utf-8")
            previous = os.environ.get("CGM_AGENT_KB_PATH")
            os.environ["CGM_AGENT_KB_PATH"] = str(kb_file)
            try:
                kb = load_knowledge_base()
            finally:
                if previous is None:
                    os.environ.pop("CGM_AGENT_KB_PATH", None)
                else:
                    os.environ["CGM_AGENT_KB_PATH"] = previous

        self.assertEqual(kb.kb_version, "custom-kb-9")
        self.assertEqual(kb.cards[0].card_id, "x")
        self.assertEqual(kb.cards[0].synonyms, ["bespoke"])

    def test_search_returns_authoritative_evidence_track(self) -> None:
        svc = AuthoritativeRAGService()
        results = svc.search("time in range target", top_k=2)
        self.assertTrue(results)
        self.assertIn("tir", results[0]["doc_id"])
        # cards carry verification state; seed cards are unverified pending review
        self.assertIn("verified", results[0])
        self.assertIn("citation", results[0])
        self.assertIn("population", results[0])
        for r in results:
            # every result is tagged authoritative_kb, never user_memory
            self.assertEqual(r["evidence_ref"]["kind"], "authoritative_kb")
            self.assertEqual(r["kb_version"], svc.kb_version)
            self.assertEqual(r["quote_instruction"], "verbatim_only")

    def test_chinese_query_recalls_bilingual_card(self) -> None:
        # D030: a Chinese query recalls an English-sourced card because cards are
        # bilingual and the tokenizer indexes CJK bigrams.
        svc = AuthoritativeRAGService()
        results = svc.search("目标范围内时间", top_k=3)
        self.assertTrue(results)
        self.assertTrue(any("tir" in r["doc_id"] for r in results))

    def test_search_can_filter_by_population(self) -> None:
        svc = AuthoritativeRAGService()
        results = svc.search(
            "time in range target",
            top_k=3,
            population="pregnancy-t1d",
        )
        self.assertTrue(results)
        # A1: filtering is by controlled class, not raw exact-string. The raw
        # population may be "pregnancy", "pregnancy-GDM", etc. — all class
        # "pregnancy" — plus the always-eligible general baseline.
        classes = {r["population_class"] for r in results}
        self.assertTrue(classes <= {"pregnancy", "general"})

    def test_normalize_population_maps_free_text_to_controlled_class(self) -> None:
        from hermes_cgm_agent.services.rag import normalize_population

        # nonpregnant must NOT match the "pregn" substring (real curated card).
        self.assertEqual(normalize_population("adult-t1d-t2d-nonpregnant"), "general")
        self.assertEqual(normalize_population("pregnancy-t1d"), "pregnancy")
        self.assertEqual(normalize_population("persons of childbearing potential"), "pregnancy")
        self.assertEqual(normalize_population("elderly T2DM with CKD G3a"), "elderly")
        self.assertEqual(normalize_population("older diabetes patients (Group 1)"), "elderly")
        self.assertEqual(normalize_population("children and adolescents"), "pediatric")
        self.assertEqual(normalize_population("hospitalized-critically-ill"), "inpatient")
        self.assertEqual(normalize_population(""), "general")
        self.assertEqual(normalize_population(None), "general")

    def test_population_filter_resolves_free_text_to_class(self) -> None:
        # A1: a free-text elderly population resolves to the controlled "elderly"
        # class; results are restricted to elderly + general, never the whole KB.
        svc = AuthoritativeRAGService()
        results = svc.search(
            "血糖管理目标",
            top_k=10,
            population="elderly T2DM with CKD G3a",
        )
        self.assertTrue(results)
        classes = {r["population_class"] for r in results}
        self.assertTrue(classes <= {"elderly", "general"})

    def test_population_filter_does_not_fail_open(self) -> None:
        # A1 regression: a population request must NOT silently return the whole
        # KB. With one pediatric and one general card, an elderly request must
        # drop the pediatric card and keep only the general baseline.
        kb = KnowledgeBase(
            kb_version="kb-pop-test",
            cards=[
                ClaimCard(
                    card_id="peds",
                    title="peds",
                    claim_zh="儿童注意事项",
                    claim_en="pediatric note about glucose",
                    population="children and adolescents",
                    source={"citation": "x", "page": 1},
                    tier="curated",
                ),
                ClaimCard(
                    card_id="gen",
                    title="gen",
                    claim_zh="通用注意事项",
                    claim_en="general note about glucose",
                    population="general",
                    source={"citation": "y", "page": 1},
                    tier="curated",
                ),
            ],
        )
        svc = AuthoritativeRAGService(knowledge_base=kb)
        results = svc.search("note about glucose", top_k=5, population="elderly")
        ids = {r["doc_id"] for r in results}
        self.assertEqual(ids, {"gen"})  # pediatric excluded; no fail-open to all

    def test_trusted_card_is_not_crowded_out_by_auto_cards(self) -> None:
        # D041 correction: noisy auto cards must never push a curated card out of
        # the top-k, even when they lexically dominate the BM25 score.
        svc = AuthoritativeRAGService(knowledge_base=_kb_with_one_curated_and_noisy_autos())
        results = svc.search("time in range target adults", top_k=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["doc_id"], "curated-tir-adults")
        self.assertEqual(results[0]["tier"], "curated")
        # Any auto card may only appear AFTER the curated one.
        first_auto = next((i for i, r in enumerate(results) if r["tier"] == "auto"), None)
        if first_auto is not None:
            self.assertGreater(first_auto, 0)

    def test_chinese_query_prefers_curated_over_auto(self) -> None:
        svc = AuthoritativeRAGService(knowledge_base=_kb_with_one_curated_and_noisy_autos())
        results = svc.search("成人目标范围内时间", top_k=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["doc_id"], "curated-tir-adults")

    def test_overlap_gate_drops_weakly_matching_auto_cards(self) -> None:
        # With a high overlap threshold, an auto card sharing only one query term
        # is dropped rather than surfaced as a background clue.
        kb = KnowledgeBase(
            kb_version="kb-overlap-test",
            cards=[
                ClaimCard(
                    card_id="auto-weak",
                    title="weak",
                    claim_zh="",
                    claim_en="range",
                    source={"citation": "x", "page": 1},
                    verified=False,
                    tier="auto",
                ),
            ],
        )
        svc = AuthoritativeRAGService(knowledge_base=kb)
        previous = os.environ.get("CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP")
        os.environ["CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP"] = "5"
        try:
            results = svc.search("time in range adults target percent value", top_k=3)
        finally:
            if previous is None:
                os.environ.pop("CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP", None)
            else:
                os.environ["CGM_AGENT_KB_MIN_UNTRUSTED_OVERLAP"] = previous
        self.assertEqual(results, [])


class RAGToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp_dir.name) / "app.db")
        self.store.initialize()
        self.session_id = "rag-test"
        self.executor = ToolExecutor(
            repository=SQLiteCGMRepository(self.store),
            audit_service=AuditService(self.store),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rag_tool_active_and_audited(self) -> None:
        body = self.executor.execute(
            tool_name="rag.authoritative_search",
            arguments={"query": "compression low false reading", "top_k": 2},
            session_id=self.session_id,
        ).to_dict()

        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["documents"])
        self.assertTrue(body["kb_version"])
        self.assertIsNotNone(body["audit_id"])
        self.assertTrue(all(ref["kind"] == "authoritative_kb" for ref in body["evidence_refs"]))
        self.assertIn("verified", body["documents"][0])
        self.assertIn("citation", body["documents"][0])
        self.assertEqual(body["quote_instruction"], "verbatim_only")

    def test_rag_tool_rejects_empty_query(self) -> None:
        body = self.executor.execute(
            tool_name="rag.authoritative_search",
            arguments={"query": "   "},
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "error")

    def test_rag_tool_rejects_string_top_k(self) -> None:
        body = self.executor.execute(
            tool_name="rag.authoritative_search",
            arguments={"query": "compression low false reading", "top_k": "2"},
            session_id=self.session_id,
        ).to_dict()

        self.assertEqual(body["status"], "error")
        self.assertIn("top_k must be an integer", body["error"])

    def test_verify_quotes_passes_when_numbers_supported(self) -> None:
        # A2: a number present in a supplied card is supported -> ok, no violations.
        body = self.executor.execute(
            tool_name="rag.verify_quotes",
            arguments={
                "generated_text": "你的目标范围内时间是 70 percent。",
                "documents": [
                    {"claim_en": "TIR target is above 70 percent", "claim_zh": "目标范围内时间应高于70%"}
                ],
                "strict": True,
            },
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["ok"])
        self.assertEqual(body["violations"], [])
        self.assertEqual(body["checked_documents"], 1)
        self.assertIsNotNone(body["audit_id"])

    def test_verify_quotes_strict_flags_unsupported_number(self) -> None:
        # A2: an unsupported number in strict mode fails the gate (ok=false).
        body = self.executor.execute(
            tool_name="rag.verify_quotes",
            arguments={
                "generated_text": "你的平均血糖是 185 mg/dL。",
                "documents": [{"claim_en": "TIR target is above 70 percent"}],
                "strict": True,
            },
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertFalse(body["ok"])
        self.assertEqual(body["mode"], "strict")
        self.assertTrue(any("185" in v for v in body["violations"]))

    def test_verify_quotes_warn_mode_lists_but_does_not_fail(self) -> None:
        # A2: default (non-strict) mode surfaces violations but ok stays true.
        body = self.executor.execute(
            tool_name="rag.verify_quotes",
            arguments={
                "generated_text": "你的平均血糖是 185 mg/dL。",
                "documents": [{"claim_en": "TIR target is above 70 percent"}],
            },
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["ok"])
        self.assertEqual(body["mode"], "warn")
        self.assertTrue(body["violations"])

    def test_verify_quotes_query_path_reretrieves_documents(self) -> None:
        # A2: with no documents, a query re-retrieves the cards to check against.
        body = self.executor.execute(
            tool_name="rag.verify_quotes",
            arguments={
                "generated_text": "目标范围内时间是一个重要指标。",  # no numbers -> ok
                "query": "time in range target",
            },
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["ok"])
        self.assertGreater(body["checked_documents"], 0)

    def test_verify_quotes_requires_documents_or_query(self) -> None:
        body = self.executor.execute(
            tool_name="rag.verify_quotes",
            arguments={"generated_text": "平均血糖 185"},
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "error")


if __name__ == "__main__":
    unittest.main()
