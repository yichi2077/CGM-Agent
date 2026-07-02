"""Memory efficacy eval tests (D053).

The eval seeds a fixture corpus into one store and leaves another empty, then
compares CGMMemoryProvider.prefetch recall of each query's expected_terms. These
tests use a tiny inline fixture so they are independent of the shipped
eval/memory corpus.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.services.memory.eval_recall import (
    evaluate_memory_recall,
    seed_fixture,
)
from hermes_cgm_agent.storage.sqlite import SQLiteStore

_FIXTURE = [
    {"kind": "l1", "days_ago": 2, "episode_type": "nocturnal_low",
     "summary": "overnight low glucose dipped to 55 夜间低血糖"},
    {"kind": "l2", "key": "noodle_sensitivity",
     "summary": "noodle causes a large post_meal_rise 面条敏感"},
    {"kind": "l3", "state": "observing", "evidence_count": 2,
     "statement": "evening walk helps overnight glucose 晚间散步"},
    {"kind": "warm", "period": "weekly", "window_days": 7,
     "content": "本周 TIR 72% dinner 偏高 overnight low 减少"},
]

_QUERIES = [
    {"query": "夜间低血糖", "expected_layer": "L1", "expected_terms": ["overnight", "low"]},
    {"query": "面条敏感吗", "expected_layer": "L2", "expected_terms": ["noodle", "post_meal_rise"]},
    {"query": "晚间散步", "expected_layer": "L3", "expected_terms": ["evening walk", "overnight"]},
    {"query": "这周怎么样", "expected_layer": "warm", "expected_terms": ["TIR", "dinner"]},
]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


class EvalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work = Path(self.temp_dir.name)
        self.fixture = self.work / "fixture.jsonl"
        self.queries = self.work / "queries.jsonl"
        _write_jsonl(self.fixture, _FIXTURE)
        _write_jsonl(self.queries, _QUERIES)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_seed_fixture_inserts_all_rows(self) -> None:
        store = SQLiteStore(self.work / "seed.db")
        store.initialize()
        n = seed_fixture(store, user_id="u", fixture_path=self.fixture)
        self.assertEqual(n, len(_FIXTURE))

    def test_with_memory_recalls_and_empty_store_does_not(self) -> None:
        report = evaluate_memory_recall(
            queries_path=self.queries, fixture_path=self.fixture
        )
        self.assertEqual(report["total"], len(_QUERIES))
        self.assertGreater(report["mean_recall_with"], report["mean_recall_without"])
        self.assertEqual(report["mean_recall_without"], 0.0)
        self.assertGreater(report["delta"], 0.0)

    def test_report_markdown_is_written(self) -> None:
        out = self.work / "report.md"
        evaluate_memory_recall(
            queries_path=self.queries, fixture_path=self.fixture, report_path=out
        )
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        self.assertIn("Memory Efficacy Report", text)
        self.assertIn("Delta", text)

    def test_min_recall_gate_semantics(self) -> None:
        # Mirrors the CLI gate: mean_recall_with below threshold should be
        # detectable by the caller (exit-1 in the CLI).
        report = evaluate_memory_recall(
            queries_path=self.queries, fixture_path=self.fixture
        )
        self.assertGreaterEqual(report["mean_recall_with"], 0.8)
        # An impossibly-high bar would fail the gate.
        self.assertLess(report["mean_recall_with"], 1.0001)


if __name__ == "__main__":
    unittest.main()
