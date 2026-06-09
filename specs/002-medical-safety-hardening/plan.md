# Implementation Plan: Medical Safety Hardening (F3)

**Branch**: `002-medical-safety-hardening` | **Date**: 2026-06-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-medical-safety-hardening/spec.md`

## Summary

Harden three medical-safety behaviors from soft constraints to code-enforced gates:

1. **Citation hard gate (B1)** — Wire `assert_authoritative_quotes` as a mandatory, non-bypassable gate in the report generation pipeline. Change default mode from `warn` to `strict`. When strict mode fails, block report delivery and return a standardized "cannot confirm" response. Run the guard on final output text only (post-generation) to prevent prompt-injection bypass.

2. **KB clinical sign-off flow (B2)** — Build a `kb.approve` tool that atomically sets `verified=true` with `reviewer` + `reviewed_at` provenance. Restrict approval to `tier=curated` cards. The CI validator already rejects `verified=true` without provenance. **KNOWN GAP**: No clinical reviewer is available this cycle; zero cards will be auto-approved. The tooling is built and ready for when a reviewer is onboarded.

3. **Red-zone recovery double-check (B3)** — Add stateful tracking of the last red-zone event timestamp per user in `SafetyRouter`. For any evaluation within 2 hours of a red-zone event, perform a second `evaluate()` call and include both results in the `SafetyDecision`. Make the 2-hour window a configurable constant.

4. **Security audit** — Produce `sec-audit.md` covering OWASP LLM Top 10 (LLM01, LLM06, LLM09 minimum) with SEC-### identifiers and mitigation plans.

## Technical Context

**Language/Version**: Python ≥3.11
**Primary Dependencies**: Pydantic v2 (domain models), `cryptography` Fernet (PHI encryption); stdlib `sqlite3`, `unittest`
**Storage**: local SQLite at canonical path (`~/.hermes/cgm-agent/app.db`); KB as packaged JSON (read-only)
**Testing**: `unittest` (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`); CI `tests.yml` + `kb-quality.yml`
**Target Platform**: local macOS/Linux behind Hermes Agent shell
**Project Type**: single project — CGM capability layer
**Performance Goals**: not a perf feature; citation guard adds negligible overhead (regex over output text)
**Constraints**: offline-capable core; DB+key `0600`; no secrets in audit/logs; do not modify `~/.hermes/hermes-agent` install tree; KB read-only except through `kb.approve`
**Scale/Scope**: single local user; ~578 KB cards (~80-100 curated); 374-test baseline

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Impact of F3 | Verdict |
|---|-----------|-------------|---------|
| I | Medical Zero-Tolerance & Authoritative Read-Only | **Core principle**: F3 hardens citation enforcement (B1), adds clinical sign-off provenance (B2), and strengthens the KB read-only invariant — `kb.approve` is the ONLY write path, and it requires clinical reviewer provenance. No cards auto-approved. | ✅ Pass — reinforced |
| II | Dual-Track Memory Isolation & One-Way Write | No changes to memory tracks. `kb.approve` writes to the KB JSON (authoritative track only) with provenance. Personal memory track untouched. Track isolation assertions remain in place. | ✅ Pass — no change |
| III | Hard-Coded Safety Routing (NON-NEGOTIABLE) | **Core principle**: F3 makes the citation gate code-enforced (B1), adds recovery double-check to `SafetyRouter` in code (B3), and ensures `kb.approve` uses strict argument validation. All safety behaviors are in code, never only in prompts. | ✅ Pass — reinforced |
| IV | Informed-Companion Persona Contract | The "cannot confirm" response when citation guard blocks must follow persona tone (gentle, non-directive, offers data-only alternative). No other persona changes. | ✅ Pass — copy reviewed against SOUL |
| V | Test-First & Green CI Gate (NON-NEGOTIABLE) | New regression tests REQUIRED: strict citation blocking (B1), `kb.approve` provenance enforcement (B2), recovery double-check (B3), security audit references. Full suite must stay green at ≥374. | ✅ Pass — enforced via tasks |
| VI | Traceable Decisions, No Phantom Docs | F3 decisions documented in `research.md`. Security audit findings have SEC-### identifiers. `DECISION_LOG` entry required for citation-gate-mode change and recovery-window design. | ✅ Pass — DECISION_LOG task included |
| VII | Hermes Boundary & Data Privacy | No changes to DB path, key management, or Hermes install tree. `kb.approve` writes to packaged KB JSON (operator action, not user PHI). Audit payloads must not leak card content or reviewer identity beyond what's needed. | ✅ Pass — no change |

**Result: PASS — no violations. KNOWN GAP: B2 clinical reviewer dependency not closable this cycle.**

## KNOWN GAP: B2 Clinical Reviewer Dependency

**Risk**: The `kb.approve` tool is built but no cards can be approved without a qualified clinical reviewer. All 578 cards remain `verified=false`. The curated seed cards (~80-100) will be ready for approval once a reviewer is available.

**Impact**: Until sign-off occurs, the citation guard (B1) has a reduced matching surface — only numbers in the existing card text (claim_zh/claim_en) are "backed", but the cards themselves are surfaced with the unverified marker. The guard still blocks unbacked numbers.

**Mitigation**: The sign-off tooling is complete and tested. The KB validator enforces provenance. The CI gate (`kb-validate`) catches any attempt to bypass. This is an operational dependency, not a code gap.

**Owner**: Project maintainer to onboard a clinical reviewer and execute `kb.approve` on curated cards.

## Project Structure

### Documentation (this feature)

```text
specs/002-medical-safety-hardening/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 — decisions
├── data-model.md        # Phase 1 — entities
├── quickstart.md        # Phase 1 — runnable validation
├── sec-audit.md         # Damocles security audit (OWASP LLM Top 10)
├── contracts/
│   └── safety-and-kb.md # Phase 1 — safety + KB tool contracts
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 — created by /speckit-tasks
```

### Source Code (affected real paths)

```text
src/hermes_cgm_agent/
├── services/
│   ├── safety/
│   │   ├── router.py           # ← F3-B3: recovery double-check, red-zone timestamp tracking
│   │   ├── citation_guard.py   # ← F3-B1: default mode → strict; integration point
│   │   └── memory_guard.py     # unchanged (KB read-only assertion)
│   ├── rag/
│   │   ├── authoritative.py    # ← F3-B2: kb.approve logic (approve method)
│   │   ├── validator.py        # ← F3-B2: unchanged (already enforces provenance)
│   │   └── tools.py            # ← F3-B1/B2: AuthoritativeRAGToolService — verify_quotes strict gate + kb.approve tool
│   ├── tools/
│   │   ├── registry.py         # ← F3-B2: register kb.approve tool schema
│   │   ├── executor.py         # ← F3-B2: kb.approve dispatch
│   │   ├── arguments.py        # unchanged (strict JSON validation already in place)
│   │   └── handlers/
│   │       ├── rag.py          # ← F3-B1/B2: _verify_quotes strict integration + _kb_approve handler
│   │       └── base.py         # unchanged
│   └── reports/
│       ├── builder.py          # ← F3-B1: wire citation guard as mandatory gate before delivery
│       └── renderer.py         # ← F3-B1/B3: "cannot confirm" template; recovery header
├── knowledge/
│   └── authoritative_kb.json   # ← F3-B2: cards updated with sign-off records (post-approval)
└── domain/
    └── cgm.py                  # ← F3-B3: (optional) RecoveryCheck dataclass

tests/
├── test_citation_guard.py      # ← F3-B1: strict mode blocking, empty text, no-verified-cards
├── test_safety_router.py       # ← F3-B3: recovery double-check, 2h window, boundary cases
├── test_kb_approve.py          # ← F3-B2: approve flow, provenance enforcement, tier restriction
└── test_report_pipeline.py     # ← F3-B1: end-to-end citation gate in report generation

specs/002-medical-safety-hardening/
└── sec-audit.md                # ← F3-US4: Damocles security audit
```

**Structure Decision**: Changes are localized to the safety, RAG, tools, and reports service layers, each with matching tests. No new packages or architectural layers. The `kb.approve` tool follows the existing `BaseToolHandler` pattern. The recovery double-check extends the existing `SafetyRouter` without changing its public API (backward-compatible addition to `SafetyDecision`).

## Phase 0 — Research

See [research.md](research.md). Resolves: citation gate mode default, kb.approve tool design, recovery window mechanism, security audit scope.

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — ClaimCard (enhanced), CitationGuardResult, SafetyDecision (enhanced), ApprovalRecord, SecurityFinding.
- [contracts/safety-and-kb.md](contracts/safety-and-kb.md) — `rag.verify_quotes` strict contract, `kb.approve` tool contract, `SafetyRouter.evaluate` recovery contract.
- [quickstart.md](quickstart.md) — end-to-end validation scenarios mapped to SC-001..SC-006.
- [sec-audit.md](sec-audit.md) — Damocles security audit (OWASP LLM Top 10).

## Complexity Tracking

| # | Principle | Violation | Justification | Risk |
|---|-----------|-----------|---------------|------|
| G1 | I (KB read-only) | `kb.approve` writes to the KB | This is the ONLY sanctioned write path; it requires reviewer provenance; the CI validator enforces it. **Correction (analyze I1)**: the current `assert_kb_readonly` is a *denylist* (`{add,write,insert,upsert,update,delete,save}`) that does NOT block `approve` — so F3 first STRENGTHENS it to catch `approve`, then explicitly allowlists `approve`. Net effect tightens, not loosens, Principle I. | LOW — single controlled write gate with provenance |
| G2 | I (verified cards) | No cards approved this cycle | External dependency (clinical reviewer); sign-off tooling is built and tested; gap documented as KNOWN GAP | MEDIUM — citation backing stays at "retrieved cards" (not verified-only) this cycle; verified-only backing DEFERRED until sign-off |

## Notes

- **Test baseline (T001, 2026-06-09)**: 374 tests green.
- **Analyze remediation (2026-06-09)**: a code-grounded `/speckit-analyze` pass found and fixed: (C1) swapped args in the citation-guard call; (I1) the `assert_kb_readonly` "rejects ANY mutator" premise was false — it is a denylist that does not block `approve`, so F3 strengthens-then-allowlists; (D1) the recovery double-check must compare stored-original vs current-on-later-call, not re-evaluate the same data / recurse; (N1) keep the function default `strict=False`, force strict only at the report gate; (I2/I3) scope the guard to medical-claim narrative (not the user's own deterministic metrics) and keep backing = retrieved cards (verified-only deferred); plus coverage tasks for the `[待核验]` marker and recovery-header rendering. See spec Clarifications 2026-06-09.
- The `assert_kb_readonly` guard is a fixed denylist; F3 adds `approve` to the blocked set AND adds an `allow_methods` exemption used only by `AuthoritativeRAGService`. This tightens Principle I (any future mutator is caught by default).
- The recovery double-check adds state to the currently stateless `SafetyRouter` (new `__init__` with `_last_red_zone`). The state is in-memory (per-process), not persisted, and is scoped to the safety service instance. This is acceptable for a single-user personal deployment. The inner re-eval uses a non-recursive `_evaluate_zone` helper.
- The security audit (`sec-audit.md`) is a documentation artifact, not code. It does not change runtime behavior but may spawn follow-up tasks if HIGH/CRITICAL findings require code changes.
