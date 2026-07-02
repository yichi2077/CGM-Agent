# ADR-0002: Device-Agnostic Simulated Source Ingest

Date: 2026-07-02

## Status

Accepted

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

## Alternatives Considered

- Extend the mock Dexcom server: rejected because Dexcom is frozen and the mock
  keeps vendor-specific behavior in the main acceptance path.
- Batch import only: rejected because it cannot validate clock-driven hourly,
  daily, memory, push, and report boundaries.
