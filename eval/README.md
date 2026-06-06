# Eval

Evaluation assets for the CGM memory and authoritative KB tracks.

## Authoritative RAG

`eval/rag/queries.jsonl` contains bilingual queries with expected claim-card ids.
Run the local hit@3 check with:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent eval-rag
# CI gate form — exit 1 if hit@3 drops below the threshold (D042):
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent eval-rag --min-hit3 0.95
```

The runner reports total queries, hit count, misses, and `hit_at_3`. It is meant
as a regression guard for the small BM25 claim-card corpus, not as a clinical
quality score. A passing retrieval result still only proves that the right card
can be found; `verified=false` cards remain draft guideline extracts.

The query set covers two things (D042): seed-card **regression** (the 6 curated
cards must stay retrievable even as `tier=auto` draft cards are merged) and
**new-card recall** (queries targeting machine-ingested cards). `--min-hit3`
makes the GitHub `kb-quality` workflow fail on a retrieval regression instead of
just printing the score.

## KB Quality Gate

Use `kb-validate` before and after merging candidate cards. It validates schema,
unique ids, source citation/page shape, and the rule that `verified=true` cards
must carry review provenance.

