# CGM-Agent Current Development Plan - 2026-06-07

## Basis

This plan supersedes the immediate execution ordering in the latest audit inputs:

- `docs/AUDIT-2026-06-07-IMPLEMENTATION-REVIEW.md`
- `docs/STRATEGY-VALIDATION-2026-06-07.md`
- `/Users/yichizhang/.claude/plans/bug-rag-refactored-pnueli.md`

The architecture direction remains valid: Hermes is the primary shell, this repo is the CGM capability layer, analytics are deterministic local tools, and LLM output is narration/orchestration rather than numeric authority.

## Current State

Closed or materially advanced since the audit baseline:

- P3 install/onboarding: `hermes-install --smoke` verifies plugin listing, Hermes memory status, and CGM `dev-status`.
- Runtime empty-state handling: `dev-status` reports `onboarding_status` and a concrete demo seed command when no glucose data exists.
- Memory historical correctness: report-generated memory candidates carry `occurred_at` and accepted memories preserve event/report time instead of review time.
- KB trust gate: authoritative report sections run strict quote verification and surface section-local warnings.
- P4 analytics depth: AGP text appendix, MODD, CONGA1/2/4, and MAGE are now implemented and tested.

## External Validation Snapshot

Current implementation remains on the correct track for a personal medical-assistant capability layer:

- Deterministic CGM calculations before LLM narration match the safer pattern for medical numeric outputs.
- MAGE is implemented as a peak/nadir excursion metric filtered by one glucose SD, not as a naive distance-from-mean metric.
- AGP work is currently textual and sufficiency-gated; this is appropriate before committing to PDF/chart rendering.
- Product positioning should stay in low-risk personal insight / clinician-preparation territory unless the project adds formal clinical validation, regulated claims, and clinician sign-off workflows.

## Rolling Priority Order

### P0 - Preserve Safety and Runtime Integrity

- Keep Hermes as the main product shell.
- Do not add a parallel general chat runtime.
- Keep clinical numbers tool-computed and quote-backed clinical statements source-verified.
- Continue to run full unit tests plus Hermes smoke after code changes.

### P1 - Product Closure Before More Horizontal Features

- Build and verify one non-empty end-to-end path: import glucose data -> analytics -> events -> report -> memory candidate -> recall.
- Add a reproducible fixture or demo dataset flow that exercises the above without relying on the developer's private runtime database.
- Promote onboarding from "needs_data" guidance to a repeatable first-run acceptance test.

### P2 - Trust and Clinical Handoff

- Add a KB review/sign-off status layer for clinical cards that distinguishes extracted, reviewed, approved, and rejected claims.
- Ensure report sections that use clinical interpretation cannot silently consume unapproved authoritative cards.
- Keep missing or unapproved source coverage visible as report warnings, not hidden fallback prose.

### P3 - Delivery Surface

- Decide the first external delivery channel: Markdown export, PDF, WeChat/App card, or Hermes-only report.
- Prefer Markdown/Hermes report first because it preserves provenance and is cheapest to verify.
- Defer polished PDF or mobile card rendering until the data and trust gates are stable.

### P4 - Analytics Hardening

- Validate MAGE against a known library or manually reviewed fixture before treating it as clinically final.
- Consider adding explicit MAGE variants later (`MAGE+`, `MAGE-`, service-direction, moving-average peak detection) only if a user-facing need requires them.
- Add AGP visual rendering only after the textual percentile appendix is stable and tested on multi-day data.

### P5 - Maintainability

- Continue reducing large-module pressure in `cli.py`, executor, and report builder.
- Extract stable service boundaries only where repeated changes prove the seam is real.
- Avoid speculative framework rewrites.

## Current Next Best Action

The next highest-value implementation target is a non-empty E2E product acceptance test and fixture-backed first-run path. The reason is simple: P4 analytics depth is now sufficient for a credible doctor-prep report, but the product still needs a repeatable "fresh install to meaningful report" proof without relying on private local state.

