# Feature Specification: CGM Simulation Pipeline

**Feature Branch**: `005-simulation-pipeline`

**Created**: 2026-07-02

**Status**: Implemented

## Overview

This feature provides a deterministic, device-agnostic CGM replay pipeline that
validates the project from CSV readings through storage, analytics, events,
memory, push, reports, and optional real Hermes preflight.

The pipeline exists to prove the product chain can run before real patient data
or live push usage. It does not make clinical efficacy claims.

## User Stories

### US1: Replay CSV Through Production Ingest

As an engineer, I can run `cgm-agent simulate --max-speed` against a CGM CSV and
produce a run-scoped DB plus `simulation_report.json`/`.md` artifacts.

Acceptance:

- CSV records are replayed in timestamp order.
- Normalization and SQLite insertion use the same production services as
  `import-cgm`.
- Duplicate readings are counted and do not crash the run.
- The audit invariant `emitted == inserted + duplicate + issues` is recorded.

### US2: Exercise Time-Driven Boundaries

As an engineer, I can validate clock-driven hourly and daily behavior without
waiting for real time.

Acceptance:

- `SimClock` supports acceleration, realtime, and max-speed modes.
- Hourly ticks compute aggregates and detected events.
- Daily ticks invoke push scheduling and memory consolidation.
- Reports are generated from the same `ReportService` path.

### US3: Guard the Real Hermes Stage

As an operator, I can request `--hermes` and receive a clear preflight result.

Acceptance:

- Missing Hermes runtime, missing DB, wrong time base, or non-importable Hermes
  dependencies return exit code 2.
- Preflight writes `hermes_stage.json`.
- The local non-Hermes simulation path remains usable in CI.

## Functional Requirements

- FR-001: The system MUST provide `cgm-agent simulate`.
- FR-002: The CLI MUST default to `examples/g0_g7_demo/cgm_14d_realistic.csv`.
- FR-003: The CLI MUST default to an isolated DB under `--out-dir` when
  `--db-path` is not supplied.
- FR-004: The source MUST support `--time-base original` and `shift-to-now`.
- FR-005: The runner MUST support `--acceleration`, `--realtime`, and
  `--max-speed`.
- FR-006: The runner MUST write JSON and Markdown audit artifacts.
- FR-007: The Hermes stage MUST use structured preflight and exit code 2 for
  environment failures.
- FR-008: Report safety routing MUST evaluate against the simulation anchor time
  instead of wall-clock time.

## Out of Scope

- Real LLM conversation quality grading.
- Email delivery.
- Dexcom mock-server expansion.
- Clinical sign-off of unverified KB cards.
