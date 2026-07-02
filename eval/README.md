# Eval

Evaluation assets for the CGM memory and authoritative KB tracks.

## Personal Memory Recall (D053)

`eval/memory/queries.jsonl` holds ~20 bilingual queries that need personal
history to answer; `eval/memory/fixture.jsonl` is the deterministic seed corpus
(L1 episodes, L2 profile beliefs, L3 hypotheses, a warm summary). The runner
seeds one store and leaves another empty, runs `CGMMemoryProvider.prefetch` on
both, and scores the fraction of each query's `expected_terms` present in the
injected context:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent eval-memory
# CI gate form — exit 1 if mean with-memory recall drops below the threshold:
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent eval-memory --min-recall 0.8 --report eval/memory/report-latest.md
```

`delta = mean_recall_with − mean_recall_without` is the evidence that the memory
subsystem — not the prompt — supplies the personalized facts. **v1 measures
context *availability* (recall), not retrieval ranking precision or answer
quality**; both are documented known gaps (no LLM is used, so the gate is
deterministic and zero-cost). `report-latest.md` is the committed evidence
artifact.

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

