#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes_cgm_agent.services.rag.eval_hit3 import evaluate_hit3


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate authoritative RAG hit@3")
    parser.add_argument(
        "--queries",
        default="eval/rag/queries.jsonl",
        help="Path to eval queries JSONL",
    )
    parser.add_argument("--kb", default=None, help="Optional KB JSON override")
    parser.add_argument(
        "--min-hit3",
        type=float,
        default=None,
        help="Fail (exit 1) if hit@3 is below this threshold, e.g. 0.95 (CI gate)",
    )
    args = parser.parse_args()
    report = evaluate_hit3(
        queries_path=Path(args.queries),
        kb_path=Path(args.kb) if args.kb else None,
    )
    if args.min_hit3 is not None:
        report["min_hit3"] = args.min_hit3
        report["passed"] = report["hit_at_3"] >= args.min_hit3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.min_hit3 is not None and report["hit_at_3"] < args.min_hit3:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
