# Quickstart / Validation: Medical Safety Hardening (F3)

Runnable validation tying each scenario to the spec's Success Criteria. Run on
the Hermes runtime venv.

Prereqs: project installed into the Hermes venv; `PYTHONPATH=src`.

## V1 — Strict citation guard blocks unbacked numbers (SC-001, US1)

1. Run citation guard unit tests:
   `... -m unittest tests.test_citation_guard -v`
2. **Expect**: strict mode blocks reports with unbacked numbers; backed numbers
   pass; empty text passes; no-verified-cards scenario blocks all.

## V2 — Citation guard integrated in report pipeline (SC-001, US1)

1. Run report pipeline tests:
   `... -m unittest tests.test_report_pipeline -v`
2. **Expect**: report with backed numbers → delivered; report with unbacked
   numbers → blocked, "cannot confirm" response; audit log records violations.

## V3 — `kb.approve` sign-off flow (SC-002, SC-006, US2)

1. Run KB approve tests:
   `... -m unittest tests.test_kb_approve -v`
2. **Expect**: approve curated card → `verified=true` + provenance; idempotent
   re-approve → no-op; auto card → rejected; missing card → error; validator
   passes after approval.

## V4 — KB validator enforces provenance (SC-006, US2)

1. Run existing KB validator tests:
   `... -m unittest tests.test_rag_validator -v`
2. **Expect**: card with `verified=true` but no `reviewer` → rejected; card with
   provenance → passes.

## V5 — Recovery double-check (SC-003, US3)

1. Run safety router tests:
   `... -m unittest tests.test_safety_router -v`
2. **Expect**: red zone → recovery window active → second evaluation; green zone
   → no recovery check; window boundary respected; recovery confirmed/not
   confirmed scenarios pass.

## V6 — Security audit exists (SC-004, US4)

1. Read `specs/002-medical-safety-hardening/sec-audit.md`.
2. **Expect**: covers ≥3 OWASP LLM Top 10 categories (LLM01, LLM06, LLM09);
   each finding has SEC-### ID, severity, mitigation; HIGH/CRITICAL findings
   have concrete actions.

## V7 — No regressions (SC-005)

1. Run full suite:
   `PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`
2. **Expect**: ≥374 tests green; new tests for citation guard, kb.approve,
   recovery check, and report pipeline all pass.

## V8 — Constitution Check holds

1. Review Constitution Check in `plan.md`.
2. **Expect**: all 7 principles pass; KNOWN GAP documented for B2 clinical
   reviewer dependency; complexity tracking items justified.

## Done = all of V1–V8 pass and Constitution Check (plan.md) still holds post-implementation.
