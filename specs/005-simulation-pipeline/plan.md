# Implementation Plan: CGM Simulation Pipeline

## Technical Summary

Add a small simulation service package that reuses existing production services:

- `clock.py`: accelerated simulation clock.
- `source.py`: `CsvReplaySource` implementing the streaming-source contract.
- `ingest.py`: per-reading normalization and repository insertion.
- `runner.py`: boundary orchestration for analytics, events, memory, push, and
  reports.
- `audit.py`: JSON/Markdown run artifacts.
- `hermes_stage.py`: guarded preflight for real Hermes execution.

## Constitution Check

- Principle I: analytics are deterministic and computed from stored CGM facts.
- Principle II: memory and authoritative KB tracks remain separate.
- Principle III: report generation still routes through `SafetyRouter`.
- Principle IV: companion text remains owned by existing report templates.
- Principle V: focused tests cover clock/source/ingest/runner/Hermes guard/CLI.
- Principle VI: ADR-0003 and D050 record the decision.
- Principle VII: default DB is isolated; canonical Hermes DB requires explicit
  `--db-path`.

## Notes

Focused verification:

```powershell
python -m unittest tests.test_sim_clock tests.test_sim_source tests.test_sim_ingest tests.test_sim_runner tests.test_simulation_hermes_e2e tests.test_cli tests.test_report_pipeline
```

Observed result during implementation: `Ran 31 tests ... OK (skipped=1)`.
