# RAG / KB Release Review - 2026-07-08

Scope: authoritative medical evidence-card expansion, deterministic enrichment,
RAG recall, report retrieval, CLI review workflow, and release usability for the
Hermes CGM Agent capability layer.

## Product Review

- Evidence cards are usable from the public CLI/tool surface:
  `rag.authoritative_search`, `rag.verify_quotes`, `reports.generate`,
  `kb-pending`, and `kb-approve`.
- The KB ships as `kb-2026-07-v3` with 580 claim cards: 574 `auto`, 6
  `curated`, and 0 clinician-verified cards. This is explicitly shown as
  unverified background evidence in tool/report outputs.
- Colloquial Chinese safety phrasing now recalls the intended safety cards. The
  probe query `血糖低了手抖怎么办` returns
  `ada-2025-hypoglycemia-levels` first.
- High-glucose/ketone phrasing returns DKA, 250 mg/dL / 13.9 mmol/L, and CDC
  ketone-testing evidence in the top results. The CDC card is still
  `verified=false` pending clinician review.
- Report generation defaults authoritative retrieval on and marks retrieved
  unverified cards as background guidance, not final medical authority.
- Clinician review remains the product gate before any card can be presented as
  verified medical guidance.

## Module Audit

- KB data: 580 cards, all `source.kb_version = kb-2026-07-v3`, no
  `verified=true`, no invented reviewer provenance.
- Ingestion: `kb-merge` enriches future auto cards deterministically with
  bilingual terms and unit dual-writing before merge.
- RAG retrieval: card-side enrichment plus query-side expansion cover common
  patient wording while preserving `eval-rag --min-hit3 0.95`.
- Reports: `reports.generate` injects authoritative context by default using
  aggregate/event-derived queries and keeps the personal-memory track separate.
- CLI: JSON input now accepts UTF-8 with or without BOM, fixing Windows
  PowerShell `Set-Content -Encoding UTF8` payloads.
- Eval: `scripts/gen_eval_queries.py` deterministically expands the eval set to
  64 authoritative RAG queries; current hit@3 is 64/64 = 1.0.

## Targeted Checks

- `kb-validate`: valid, no problems.
- `eval-rag --min-hit3 0.95`: passed, 1.0 hit@3.
- Full test suite: 575 tests OK, skipped=2.
- CLI probes:
  - `kb-pending --format json --limit 3` returns pending card metadata only.
  - `rag.authoritative_search` retrieves low-glucose, ketone/DKA,
    compression-low, and data-quality cards for representative queries.
  - `rag.verify_quotes` strict mode passes the supported 15 g / 15 minute
    hypoglycemia sentence.
  - `reports.generate` works without explicit `retrieve_context` and includes
    authoritative KB evidence refs.

## Residual Release Risks

- No cards are clinically signed off yet. This is an explicit product limitation,
  not an engineering gap.
- Pediatric `0.3 g/kg` low-glucose treatment was not added because no
  source-backed card for that exact claim was present in the current KB/review
  queue search.
- One generated elderly TBR eval query still misses its exact expected auto card
  while retrieving closely related general/older high-risk evidence; the release
  gate remains comfortably above threshold.
