from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.audit import AuditService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.rag import AuthoritativeRAGService, load_knowledge_base
from hermes_cgm_agent.services.tools import ToolExecutor
from hermes_cgm_agent.storage.sqlite import SQLiteStore


class AuthoritativeRAGTests(unittest.TestCase):
    def test_knowledge_base_loads_with_version(self) -> None:
        kb = load_knowledge_base()
        self.assertTrue(kb.kb_version)
        self.assertTrue(kb.documents)

    def test_env_var_override_loads_custom_kb(self) -> None:
        # C7: an operator can point the loader at an explicit KB file, and the
        # default load must resolve from packaged data (not a repo-root guess).
        import os

        custom = {
            "kb_version": "custom-kb-9",
            "documents": [
                {"doc_id": "x", "title": "Custom", "text": "custom entry", "tags": []}
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
        self.assertEqual(kb.documents[0].doc_id, "x")

    def test_search_returns_authoritative_evidence_track(self) -> None:
        svc = AuthoritativeRAGService()
        results = svc.search("time in range target", top_k=2)
        self.assertTrue(results)
        self.assertEqual(results[0]["doc_id"], "tir-consensus")
        for r in results:
            # every result is tagged authoritative_kb, never user_memory
            self.assertEqual(r["evidence_ref"]["kind"], "authoritative_kb")
            self.assertEqual(r["kb_version"], svc.kb_version)


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

    def test_rag_tool_rejects_empty_query(self) -> None:
        body = self.executor.execute(
            tool_name="rag.authoritative_search",
            arguments={"query": "   "},
            session_id=self.session_id,
        ).to_dict()
        self.assertEqual(body["status"], "error")


if __name__ == "__main__":
    unittest.main()
