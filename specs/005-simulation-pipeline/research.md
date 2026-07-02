# Research: CGM Simulation Pipeline

## Decision: Do Not Use the Frozen Dexcom Mock as the Acceptance Backbone

Dexcom integration remains useful regression coverage, but the acceptance path
for pre-user validation should be vendor independent. The new pipeline treats a
CSV as a streaming source and feeds normalized points into the same repository
used by production paths.

## Decision: Preserve Native CSV Cadence

The replay source does not interpolate. The source emits exactly the rows that
exist in the CSV, ordered by recorded timestamp. Gaps remain visible to
normalization and event detection.

## Decision: Shift Only When Needed

`--time-base original` is deterministic and preferred for CI. `shift-to-now` is
needed for the Hermes stage because freshness and safety recovery checks use
wall-clock-relative windows.

## Time Injection Audit

The implementation passes simulation time into:

- `PushSchedulerService.push_tick(now=...)`
- `ConsolidationService.consolidate(now=...)`
- `ConsolidationService.synthesize_state(now=...)`
- `ReportInput.anchor_at`
- `SafetyRouter.evaluate(now=ReportInput.anchor_at)`

Repository `created_at` timestamps remain wall-clock audit facts by design.
