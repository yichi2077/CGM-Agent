# ADR-0003: Device-Agnostic Simulated Source Ingest

Date: 2026-07-02

## Status

Accepted

> Note: This ADR was renumbered from a draft ADR-0002 to ADR-0003 to resolve a
> numbering collision with `ADR-0002-cgm-data-source-strategy.md`, which owns the
> F2/E2 data-source-strategy decision. This ADR covers only the simulation /
> validation ingest path introduced by Feature 005.

## Context

The project needs a repeatable 0-to-1 CGM validation path before any real user
push. The existing Dexcom path is frozen, and the mock Dexcom server is not an
appropriate acceptance backbone because it carries vendor-specific assumptions
and wall-clock coupling.

## Decision

Add a device-agnostic simulation ingest path under
`hermes_cgm_agent.services.simulation`.

The first implementation is CSV replay:

- `CsvReplaySource` emits sorted `RawCGMRecord` objects at their simulated
  measurement timestamps.
- `StreamIngestor` reuses `CGMNormalizer` and `SQLiteCGMRepository`; it does not
  maintain a second ingest implementation.
- `SimulationRunner` advances a `SimClock`, ingests each reading, and triggers
  analytics, detected events, push ticks, memory consolidation, and reports.
- `cgm-agent simulate` defaults to an isolated DB under `.runtime/simulation/*`
  unless the operator explicitly supplies `--db-path`.
- Real Hermes execution is guarded by `HermesStage.preflight`; a missing or
  mismatched runtime returns exit code 2, not a fake success.

## Consequences

- The acceptance path is independent of frozen Dexcom code while still using the
  same normalization, repository, analytics, memory, push, and report services.
- The default CLI path is safe for local experimentation because it does not
  write to the canonical Hermes DB unless explicitly requested.
- `--time-base shift-to-now` is required for the Hermes stage because downstream
  freshness checks are wall-clock based.
- The simulation audit writes both JSON and Markdown artifacts so failed runs
  leave concrete evidence.

## Long-run acceptance contract

The same runner is the 24-72 hour accelerated soak backbone. Its JSON report is
machine-authoritative and contains:

- a run-scoped, ordered `timeline` with correlation IDs for ingest, hourly
  analytics/events, memory, reports, and push stages;
- explicit cross-stage `links` so event and report outputs can be traced to the
  stage/window that produced them;
- durable `pipeline_counts` for L1/L2/L3, warm summaries, and reports;
- `acceptance.comparisons` with expected and actual values, plus a single
  `acceptance.passed` decision used as the process exit gate;
- duplicate/loss accounting and a same-database replay check that proves a
  restart does not reproduce downstream memory, reports, or pushes.

Pull requests use a fast deterministic smoke gate. The full suite runs on
push/nightly, while `.github/workflows/simulation-soak.yml` is the explicit
24-72 hour accelerated acceptance workflow and retains its reports as CI
artifacts.

## Alternatives Considered

- Extend the mock Dexcom server: rejected because Dexcom is frozen and the mock
  keeps vendor-specific behavior in the main acceptance path.
- Batch import only: rejected because it cannot validate clock-driven hourly,
  daily, memory, push, and report boundaries.
