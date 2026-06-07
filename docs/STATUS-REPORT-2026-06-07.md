# CGM-Agent Current Status Report

- Date: 2026-06-07
- Scope: runtime stability baseline + audit starting point
- Evidence command set:
  - `hermes plugins list --plain --no-bundled`
  - `hermes memory status`
  - `PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m hermes_cgm_agent dev-status`
  - `PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests`

## Summary

The project is currently in the memory/RAG product-loop phase. The Hermes
plugin runtime is discoverable, the CGM memory provider is available, and the
Hermes venv test suite passes. The next engineering priority is to keep runtime
truth, documentation, and module boundaries aligned while continuing the
memory/RAG correctness audit.

## Verified Current Facts

- Hermes runtime: `Hermes Agent v0.15.1 (2026.5.29)`.
- Main shell: Hermes runtime; local CLI remains an engineering support surface.
- Runtime DB path from `dev-status`: `/Users/yichizhang/code/CGM-Agent/.runtime/app.db`.
- Tool registry: `tool_count: 14`, `planned_tool_count: 0`, `active_tool_count: 14`.
- Hermes plugin list:
  - `cgm` is enabled as a user plugin.
  - `cgm_memory` appears as `not enabled` in the plugin list but is installed and active through Hermes memory provider configuration.
- Hermes memory status:
  - Provider: `cgm_memory`.
  - Plugin: installed.
  - Status: available.
  - `cgm_memory` is marked active.
- Test baseline: Hermes venv unit tests pass with `314 tests OK`.
- Real Hermes tool-call smoke:
  - Command: `hermes chat --toolsets cgm -q '...' -Q --max-turns 3`.
  - Tool used: `cgm_rag_authoritative_search`.
  - Result: 3 authoritative KB documents returned.
  - Returned KB version: `kb-2026-06-auto-v2`.
  - Returned verification state: all observed documents had `verified=false`.
- Report-to-memory review loop smoke:
  - `reports.generate` can enqueue `g8_memory_candidates` into the memory candidate queue.
  - `memory.list` now supports `layer="candidates"` so pending candidates are discoverable through the public tool surface.
  - Real Hermes `cgm_memory_list` smoke with `layer=candidates` returned `status=ok`, `candidate_count=117`, and `total_count=0`.
  - `memory.confirm` can accept a returned `candidate_id` and promote the accepted candidate to L1 memory.
- Empty-window report smoke:
  - `reports.generate` no longer renders `None mg/dL` when a window has zero valid CGM points.
  - The metrics section now states that key indicators are not yet computable until valid data exists.
  - Empty windows no longer emit G8 memory candidates or enqueue unsupported "continue observing" hypotheses.
- USER.md managed-section sync smoke:
  - L2 sync replaces only the CGM managed block.
  - User text before and after the managed block is preserved.
  - Stale managed-block content is removed, and only one start/end marker pair remains.
- Engineering structure audit:
  - `ToolExecutor._memory_list` no longer owns candidate queue retrieval and serialization inline.
  - Candidate queue listing is isolated in a helper with the default `pending` filter and explicit `candidate_status="all"` audit path covered by tests.
  - Optional boolean tool flags now reject string values such as `"false"` instead of relying on Python truthiness.
  - `reports.generate retrieve_context` and `memory.list include_archived` are covered by regression tests for strict boolean parsing.
  - Integer tool flags now reject string values such as `"7"` instead of coercing them through `int(...)`.
  - `timeseries.get_points limit`, `rag.authoritative_search top_k`, and `data.dexcom_sync days` are covered by strict integer parsing tests.
  - Memory tool enum fields now match their schemas exactly instead of accepting lowercase or whitespace variants.
  - `memory.list layer`, `memory.list candidate_status`, `memory.delete layer`, and `memory.correct target` are covered by strict enum parsing tests.
  - `memory.correct` correction fields now reject string coercion for numeric and boolean write paths.
  - `correction.confidence`, `correction.archive`, and `correction.deactivate` are covered by write-path validation tests.
  - Durable memory text/object/state fields now reject incorrect JSON types before persistence.
  - `correction.summary`, `correction.value`, `correction.statement`, and `correction.state` are covered by strict write-path tests.
  - Shared tool argument validators have been extracted from `ToolExecutor` into `services/arguments.py`, with `services/tools/arguments.py` retained as a compatibility re-export.
  - Strict bool/int/enum parsing now has direct helper-level tests in addition to end-to-end tool tests.
  - `memory.list` / `memory.delete` business logic has been extracted from `ToolExecutor` into `MemoryToolService`.
  - Memory tool list/delete behavior now has direct service-level tests in addition to executor tool tests.
  - `memory.confirm` and report `g8_memory_candidates` ingestion have also moved into `MemoryToolService`.
  - Candidate confirmation and report-to-candidate ingestion now have direct service-level tests in addition to executor tool tests.
  - `memory.correct` durable-memory correction and L2 USER.md sync triggering have moved into `MemoryToolService`.
  - Memory correction behavior now has direct service-level tests for L1 updates, L2 sync-on-Hermes-home, and no-sync-without-Hermes-home.
  - `reports.generate` tool orchestration has moved into `ReportToolService`.
  - Report tool generation, default memory auto-ingest policy, doctor-report no-auto-ingest policy, and strict `retrieve_context` parsing now have direct service-level tests.
  - `rag.authoritative_search` tool orchestration has moved into `AuthoritativeRAGToolService`.
  - RAG tool request parsing, KB evidence refs, population filtering, strict `top_k`, empty-query rejection, and query-number coverage hints now have direct service-level tests.
  - `data.dexcom_sync` tool orchestration has moved into `DexcomSyncToolService`.
  - Dexcom sync tool request parsing, default days/force behavior, strict `force`, and days range validation now have direct service-level tests.
  - `hypothesis.update` L3 state mutation has moved into `MemoryToolService`.
  - Hypothesis update now has direct service-level tests for state change, evidence merge, user scoping, strict state enum parsing, and non-list evidence rejection.
  - `events.confirm` tool orchestration has moved into `EventToolService`.
  - Event confirmation now has direct service-level tests for promotion, rejection, user scoping, strict `confirmed`, and non-object `correction` rejection.
  - `ReportService._patterns_section` no longer owns pattern signal selection inline; pattern text, evidence, and memory-candidate eligibility are isolated in `PatternSignal` / `_pattern_signal`.

## Audit Findings Opened

- Older reports still contain historical counts (`183`, `196`, `222`, and `13 active tools`). Treat those as dated snapshots, not current status.
- The repository-local `.venv` is absent; the reliable test path is Hermes' runtime venv.
- Hermes `chat -Q` still emitted a Reasoning block in this environment during the tool-call smoke. The tool call succeeded, but fully programmatic smoke checks should not depend on quiet output being response-only until this is understood.
- Several core modules are large enough to require boundary review before the next major feature push:
  - `src/hermes_cgm_agent/cli.py`
  - `src/hermes_cgm_agent/services/tools/executor.py`
  - `src/hermes_cgm_agent/services/reports/builder.py`
  - `src/hermes_cgm_agent/services/memory/repository.py`
- `eval-rag` tests previously exercised a CLI helper that printed reports to stdout during unit tests. This has been corrected by adding a quiet internal path while preserving command-line output.
- `reports.generate` previously queued pending candidates, but `memory.list` did not expose the candidate queue; users could not discover the `candidate_id` needed by `memory.confirm`. This has been corrected by extending `memory.list` with candidate output.
- Hermes plugin integration tests now assert that both the CGM tool plugin schema and the memory provider wrapper schema expose `layer="candidates"` and the `candidate_status` enum.
- Empty CGM windows previously rendered `平均大约 None mg/dL` in the metrics section. This has been corrected with an explicit no-computable-metrics branch.
- Empty CGM windows previously emitted a low-confidence L3 "continue observing" candidate despite having no valid data. This has been corrected so no long-term memory candidate is produced without evidence.
- `ToolExecutor` remains large, but the memory candidate listing contract is now isolated enough to support the next extraction step without changing external tool behavior.
- `ToolExecutor` previously used Python truthiness for optional boolean flags in `reports.generate` and `memory.list`; a string `"false"` would enable the feature. This has been corrected with strict optional boolean parsing.
- `ToolExecutor` previously coerced schema-integer tool arguments with `int(...)`; string values could cross the boundary as accepted integers. This has been corrected for shared `limit`, RAG `top_k`, and Dexcom `days`.
- `ToolExecutor` previously accepted lowercase or whitespace variants for some memory enum fields even though the schema advertised exact enum values. This has been corrected for memory layer, correction target, and candidate status.
- `MemoryReviewService.correct` previously used `float(...)` and `bool(...)` on correction fields; strings such as `"0.9"` or `"false"` could alter durable memory. This has been corrected with strict number/boolean validators.
- `MemoryReviewService.correct` previously allowed non-string summaries/statements and non-object L2 values to reach durable memory replacement paths. This has been corrected with explicit string/object/state validators.
- `ToolExecutor` previously owned shared primitive argument validators inline. Those validators are now isolated in a dedicated service-level module, reducing the risk that future tools reintroduce Python coercion semantics.
- The first extraction of shared validators into `services/tools/arguments.py` exposed a package-cycle risk once report tool orchestration moved into `services/reports`. The canonical implementation now lives at `services/arguments.py` so non-tool packages can use strict JSON-boundary parsing without importing executor.
- `ToolExecutor` previously owned memory listing/deletion business logic inline. `MemoryToolService` now owns that capability-layer behavior so executor can stay focused on tool routing, audit, and response envelopes.
- `ToolExecutor` previously owned memory candidate confirmation and report-candidate ingestion inline. These write paths are now part of `MemoryToolService`, keeping candidate queue behavior in the memory capability layer.
- `ToolExecutor` previously owned `memory.correct` write behavior and the L2 USER.md sync side effect inline. These have moved into `MemoryToolService`, keeping executor focused on argument validation, audit logging, and response envelopes.
- `ToolExecutor` previously owned `reports.generate` orchestration inline, including `ReportInput` construction, RAG context injection, and report memory auto-ingest policy. These have moved into `ReportToolService`; executor now keeps the audit envelope and `audit_id` persistence handoff.
- `ToolExecutor` previously owned `rag.authoritative_search` orchestration inline, including query normalization, `top_k` parsing, KB service lifecycle, evidence-ref extraction, and query-number coverage payload shaping. These have moved into `AuthoritativeRAGToolService`; executor now keeps only audit logging and response envelope assembly.
- `ToolExecutor` previously owned `data.dexcom_sync` parsing and sync service invocation inline. These have moved into `DexcomSyncToolService`; executor keeps authorization/error response handling, audit logging, and response envelope assembly. The sync audit payload remains count/window/environment-only and does not include Dexcom tokens or secrets.
- `ToolExecutor` previously owned `hypothesis.update` L3 mutation inline, including state parsing, evidence validation, user-scoped lookup, evidence-count updates, and timestamp updates. These have moved into `MemoryToolService`; executor now keeps audit logging and response envelope assembly.
- `ToolExecutor` previously owned `events.confirm` write orchestration inline, including strict boolean parsing, correction object validation, and repository confirmation calls. These have moved into `EventToolService`; executor now keeps audit logging and response envelope assembly.
- `ReportService` remains large, but pattern signal selection is now separated from `ReportSection` construction, reducing the risk of future report-copy changes accidentally changing memory candidate eligibility.

## Next Review Gates

1. Runtime stability: keep Hermes plugin discovery, memory provider status, and `dev-status` in agreement.
2. Memory/RAG correctness: verify candidate queue closure, USER.md managed-section sync, and authoritative-card citation behavior with real tool calls.
3. Engineering quality: continue splitting large modules only where responsibility boundaries are already clear and covered by tests.
4. Tool argument hardening: keep auditing string coercion, enum parsing, and tool error paths before exposing more write-capable actions.
