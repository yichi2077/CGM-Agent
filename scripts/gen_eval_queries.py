#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


SAFETY_KEYWORDS = (
    "hypoglycemia",
    "低血糖",
    "hyperglycemia",
    "高血糖",
    "ketone",
    "酮",
    "dka",
    "酮症酸中毒",
    "tir",
    "tbr",
    "tar",
    "gmi",
    "cv",
    "pregnancy",
    "妊娠",
    "pediatric",
    "儿童",
    "elderly",
    "老年",
)

ALWAYS_INCLUDE_CARD_IDS = {
    "cdc-2024-dka-ketone-testing-250-sick-day",
    "cdc-2024-alcohol-nighttime-hypoglycemia",
}


def _words(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.replace("；", ";").replace("，", ",").split() if part.strip()]


GENERIC_SYNONYMS = {
    "血糖低了",
    "低血糖了怎么办",
    "手抖",
    "发慌",
    "出冷汗",
    "血糖太低",
    "血糖太高",
    "要不要查酮体",
    "血糖高怎么办",
    "运动会不会低血糖",
    "锻炼血糖",
    "跑步血糖",
    "运动后血糖",
}


def _compact_terms(card: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for field in ("title", "population"):
        text = str(card.get(field) or "").strip()
        if text:
            terms.append(text)
    tags = card.get("tags") or []
    if isinstance(tags, list):
        terms.extend(str(v).strip() for v in tags if str(v).strip())
    for field in ("claim_zh", "claim_en"):
        terms.extend(_words(card.get(field))[:10])
    synonyms = card.get("synonyms") or []
    if isinstance(synonyms, list):
        terms.extend(
            str(v).strip()
            for v in synonyms
            if str(v).strip() and str(v).strip() not in GENERIC_SYNONYMS
        )

    seen: set[str] = set()
    deduped: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(term)
    return deduped


def _is_priority_card(card: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(card.get("title") or ""),
            str(card.get("claim_zh") or ""),
            str(card.get("claim_en") or ""),
            " ".join(str(v) for v in card.get("tags") or []),
            " ".join(str(v) for v in card.get("synonyms") or []),
            str(card.get("population") or ""),
        ]
    ).casefold()
    return card.get("tier") == "curated" or any(keyword.casefold() in haystack for keyword in SAFETY_KEYWORDS)


def _priority_score(card: dict[str, Any]) -> int:
    haystack = " ".join(
        [
            str(card.get("title") or ""),
            str(card.get("claim_zh") or ""),
            str(card.get("claim_en") or ""),
            " ".join(str(v) for v in card.get("tags") or []),
            " ".join(str(v) for v in card.get("synonyms") or []),
        ]
    ).casefold()
    score = 0
    for keyword in ("dka", "ketone", "酮", "hypoglycemia", "低血糖", "glucagon", "胰高血糖素"):
        if keyword.casefold() in haystack:
            score += 3
    for keyword in ("250", "13.9", "70", "54", "15-15", "15 g", "15 克"):
        if keyword.casefold() in haystack:
            score += 2
    for keyword in ("sick day", "病假日", "exercise", "运动", "data quality", "数据质量"):
        if keyword.casefold() in haystack:
            score += 1
    return score


def _query_variants(card: dict[str, Any], *, queries_per_card: int) -> list[dict[str, Any]]:
    card_id = str(card["card_id"])
    terms = _compact_terms(card)
    if not terms:
        return []
    population = str(card.get("population") or "general").strip() or "general"
    rows: list[dict[str, Any]] = []
    base = " ".join(terms[:8])
    rows.append(
        {
            "query": base,
            "expected_any": [card_id],
            "track": "authoritative_kb",
            "population": population if population != "general" else None,
            "generated_by": "scripts/gen_eval_queries.py",
        }
    )
    bilingual = " ".join(terms[8:18] or terms[:8])
    if bilingual != base:
        rows.append(
            {
                "query": bilingual,
                "expected_any": [card_id],
                "track": "authoritative_kb",
                "population": population if population != "general" else None,
                "generated_by": "scripts/gen_eval_queries.py",
            }
        )
    return rows[: max(1, queries_per_card)]


def _row_key(row: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return (
        str(row.get("query") or "").strip().casefold(),
        tuple(sorted(str(v) for v in row.get("expected_any") or [])),
    )


def load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            if row.get("generated_by") == "scripts/gen_eval_queries.py":
                continue
            rows.append(row)
    return rows


def generate_queries(
    kb_path: Path,
    *,
    existing: list[dict[str, Any]] | None = None,
    max_cards: int = 8,
    queries_per_card: int = 2,
) -> list[dict[str, Any]]:
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    rows = list(existing or [])
    seen = {_row_key(row) for row in rows}

    cards = [card for card in kb.get("cards", []) if isinstance(card, dict) and _is_priority_card(card)]
    cards.sort(key=lambda c: (c.get("tier") != "curated", -_priority_score(c), str(c.get("card_id") or "")))
    selected = cards[:max_cards]
    selected_ids = {str(card.get("card_id") or "") for card in selected}
    selected.extend(
        card
        for card in cards[max_cards:]
        if str(card.get("card_id") or "") in ALWAYS_INCLUDE_CARD_IDS
        and str(card.get("card_id") or "") not in selected_ids
    )
    for card in selected:
        if not card.get("card_id"):
            continue
        for row in _query_variants(card, queries_per_card=queries_per_card):
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic authoritative RAG eval queries")
    parser.add_argument("--kb", default="src/hermes_cgm_agent/knowledge/authoritative_kb.json")
    parser.add_argument("--out", default="eval/rag/queries.jsonl")
    parser.add_argument("--preserve-existing", action="store_true", default=True)
    parser.add_argument("--replace", action="store_true", help="Do not preserve existing query rows")
    parser.add_argument("--max-cards", type=int, default=8)
    parser.add_argument("--queries-per-card", type=int, default=2)
    args = parser.parse_args()

    out_path = Path(args.out)
    existing = [] if args.replace else load_existing(out_path)
    rows = generate_queries(
        Path(args.kb),
        existing=existing,
        max_cards=args.max_cards,
        queries_per_card=args.queries_per_card,
    )
    write_jsonl(out_path, rows)
    print(json.dumps({"status": "ok", "query_count": len(rows), "out": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
