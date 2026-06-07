from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hermes_cgm_agent.cli import _eval_rag
from hermes_cgm_agent.services.rag.eval_hit3 import evaluate_hit3


def _write_half_hit_fixture(temp_dir: str) -> tuple[Path, Path]:
    kb_path = Path(temp_dir) / "kb.json"
    kb_path.write_text(
        json.dumps(
            {
                "kb_version": "kb-eval",
                "cards": [
                    {
                        "card_id": "battelino-2019-tir-adults",
                        "title": "TIR adults",
                        "claim_zh": "成人 TIR 目标 >70%",
                        "claim_en": "For adults TIR target >70%",
                        "tags": ["TIR", "targets"],
                        "synonyms": ["time in range"],
                        "source": {"citation": "DC 2019", "page": 16},
                        "verified": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    queries_path = Path(temp_dir) / "queries.jsonl"
    queries_path.write_text(
        "\n".join(
            [
                json.dumps({"query": "成人 TIR 目标", "expected_any": ["battelino-2019-tir-adults"]}),
                json.dumps({"query": "unrelated xyz", "expected_any": ["missing-card"]}),
            ]
        ),
        encoding="utf-8",
    )
    return queries_path, kb_path


class EvalRagTests(unittest.TestCase):
    def test_hit3_computation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            kb_path = Path(temp_dir) / "kb.json"
            kb_path.write_text(
                json.dumps(
                    {
                        "kb_version": "kb-eval",
                        "cards": [
                            {
                                "card_id": "battelino-2019-tir-adults",
                                "title": "TIR adults",
                                "claim_zh": "成人 TIR 目标 >70%",
                                "claim_en": "For adults TIR target >70%",
                                "tags": ["TIR", "targets"],
                                "synonyms": ["time in range"],
                                "source": {"citation": "DC 2019", "page": 16},
                                "verified": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            queries_path = Path(temp_dir) / "queries.jsonl"
            queries_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "query": "成人 TIR 目标",
                                "expected_any": ["battelino-2019-tir-adults"],
                            }
                        ),
                        json.dumps(
                            {
                                "query": "unrelated xyz",
                                "expected_any": ["missing-card"],
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            report = evaluate_hit3(queries_path=queries_path, kb_path=kb_path)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["hits"], 1)
        self.assertEqual(report["hit_at_3"], 0.5)

    def test_min_hit3_gate_fails_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queries_path, kb_path = _write_half_hit_fixture(temp_dir)
            # hit@3 is 0.5; a 0.9 floor must fail (exit 1), a 0.4 floor must pass.
            self.assertEqual(
                _eval_rag(
                    queries_path=queries_path,
                    kb_path=kb_path,
                    min_hit3=0.9,
                    emit_report=False,
                ),
                1,
            )
            self.assertEqual(
                _eval_rag(
                    queries_path=queries_path,
                    kb_path=kb_path,
                    min_hit3=0.4,
                    emit_report=False,
                ),
                0,
            )
            # No threshold => always 0 (report-only).
            self.assertEqual(
                _eval_rag(
                    queries_path=queries_path,
                    kb_path=kb_path,
                    min_hit3=None,
                    emit_report=False,
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
