# Tasks: CGM Simulation Pipeline

## Completed

- [X] T001 Add `SimClock` with acceleration, realtime-compatible, and max-speed
  behavior.
- [X] T002 Add `CsvReplaySource` with sorted replay, `original`, `shift-to-now`,
  and `--days` truncation.
- [X] T003 Add `StreamIngestor` that reuses `CGMNormalizer` and
  `SQLiteCGMRepository`.
- [X] T004 Add `SimulationAudit` JSON/Markdown artifacts.
- [X] T005 Add `SimulationRunner` boundary orchestration.
- [X] T006 Move detected-event to L1 derivation into `services/memory/derive.py`
  while preserving the CLI compatibility wrapper.
- [X] T007 Pass report `anchor_at` into `SafetyRouter.evaluate(now=...)`.
- [X] T008 Add `HermesStage` preflight with exit code 2 for runtime failures.
- [X] T009 Add `cgm-agent simulate` CLI.
- [X] T010 Add focused tests for clock/source/ingest/runner/Hermes guard/CLI.
- [X] T011 Add ADR-0003, D050, and Spec-Kit artifacts.
- [X] T012 (2026-07-05, D051) Real-run hardening: infer device cadence from the
  replayed data (override with `--expected-interval-min`), cap
  `data_coverage` at 100 (dense 1-minute feeds crashed aggregate validation),
  and record wrap-up-stage failures into the audit instead of aborting without
  writing `simulation_report.json`.

## Deferred

- [ ] Full live Hermes AIAgent conversation validation; guarded by
  `CGM_RUN_HERMES_E2E=1`.
- [ ] Email delivery acceptance.
- [ ] AGP percentile visualization.
