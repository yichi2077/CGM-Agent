<!--
SYNC IMPACT REPORT
==================
Version change: (template) → 1.0.0
Bump rationale: Initial ratification of the project constitution. First concrete
  fill of the bundled template, so this is a MAJOR establishment (1.0.0).

Principles defined (7):
  I.   Medical Zero-Tolerance & Authoritative Read-Only
  II.  Dual-Track Memory Isolation & One-Way Write Protection
  III. Hard-Coded Safety Routing (NON-NEGOTIABLE)
  IV.  Informed-Companion Persona Contract
  V.   Test-First & Green CI Gate (NON-NEGOTIABLE)
  VI.  Traceable Decisions, No Phantom Docs
  VII. Hermes Boundary & Data Privacy

Added sections:
  - Architecture & Technology Baseline
  - Development Workflow & Quality Gates
  - Governance

Templates reviewed for alignment:
  ✅ .specify/templates/plan-template.md   — generic "Constitution Check" gate; compatible, no edit needed
  ✅ .specify/templates/spec-template.md    — scope/requirements sections compatible
  ✅ .specify/templates/tasks-template.md   — task categories compatible; add test-first + safety tasks per feature
  ✅ .specify/templates/checklist-template.md — compatible

Deferred / TODO: none. RATIFICATION_DATE set to first formal adoption (2026-06-08).

Source invariants consolidated from: SOUL.md, AGENTS.md, docs/adr/ADR-0001,
docs/DECISION_LOG.md (D013/D015/D022/D027–D044), docs/MEM-ARCH.md, and the
2026-06-07 Damocles security audit.
-->

# Hermes CGM Agent Constitution

The Hermes CGM Agent is a personal continuous-glucose-monitoring (CGM) capability
layer that runs behind the Hermes Agent shell. It handles real health data for
real people. These principles are non-negotiable engineering law: every spec,
plan, task, code review, and merge MUST comply. Where a principle says MUST or
MUST NOT, a violation is a release blocker, not a preference.

## Core Principles

### I. Medical Zero-Tolerance & Authoritative Read-Only

The agent MUST NOT invent, restate from memory, or alter clinical numbers.

- All numeric clinical metrics (TIR/TAR/TBR/GMI/CV/LBGI/HBGI, glucose event
  detection) MUST be computed by deterministic analytics code, never produced or
  rewritten by an LLM. (D013, D015, D022)
- The authoritative medical knowledge base is **read-only to the agent**: there
  is no write API, and the read-only invariant MUST stay wired into
  `AuthoritativeRAGService` (`assert_kb_readonly`). (D028, D031)
- Knowledge cards with `verified=false` MUST be surfaced as unverified drafts and
  MUST NOT be presented in an authoritative clinical voice. A card may only become
  `verified=true` after recorded external review provenance.
- Medical claims MUST carry verbatim source + page/section citation. The
  anti-hallucination quote check (`rag.verify_quotes`) is a mandatory contract,
  not a hint.

**Rationale:** This is a medical system with zero tolerance for fabricated
thresholds or dosing facts; the cheapest defense is to make fabrication
structurally impossible.

### II. Dual-Track Memory Isolation & One-Way Write Protection

Personal memory and medical memory have opposite lifecycles and MUST stay
physically isolated by policy, not merely labeled. (D027)

- Personal memory MUST NEVER be written into medical memory.
- The two evidence tracks (`user_memory` vs `authoritative_kb`) MUST NEVER be
  merged in retrieval or report assembly; the isolation assertion
  (`assert_track_isolation`) MUST run wherever both tracks are injected.
- On conflict between a personal belief and a medical fact, the medical fact wins,
  and the generation layer MUST present it gently without negating the user
  (`resolve_conflict`). (D031)
- Personal memory is bi-temporal: L2/L3 items carry `valid_from`/`valid_to` and
  `source_episode_ids` lineage; supersede closes the old window rather than
  deleting it. (D032)

**Rationale:** Cross-contamination between "verified truth" and "hints to be
verified" is the single highest-risk failure mode of this architecture.

### III. Hard-Coded Safety Routing (NON-NEGOTIABLE)

Safety MUST be enforced in code, never only in a prompt.

- Glucose values route through the safety router (red / yellow / green zones)
  before any narrative is emitted.
- Red zone MUST replace report sections wholesale (zero narrative leakage), MUST
  NOT collect memory candidates, and MUST NOT push digests.
- Yellow zone prepends a visible alert but may proceed with normal narrative.
- The agent MUST NOT diagnose, MUST NOT prescribe, and MUST NOT give medication
  dosing — in any zone, in any persona variant.

**Rationale:** Soft prompt-level guardrails fail silently under distribution
shift; a hard-coded gate fails loudly and testably.

### IV. Informed-Companion Persona Contract

Every user-facing utterance follows the SOUL.md "Informed Companion" persona.

- Non-directive: MUST NOT use "你应该 / 你必须 / 你需要 / 建议你"; invite with
  questions and conditionals instead.
- Explicit uncertainty: individual-pattern claims MUST use hedged language
  ("看起来 / 可能 / 在你的记录中"); never causal/clinical assertions about the user.
- History before knowledge: answer first from the user's own data and prior
  turns, then aggregate metrics, and only cite authoritative knowledge on request.
- No judgment; emotion before data when the user expresses feelings.
- Default output is short, conversational Chinese (≈30–80 chars). Doctor-version
  and family-version reports are generated ONLY on explicit user request/trigger,
  never by default.

**Rationale:** The product's success metric is whether the user *wants* to tell
the agent what happened. Tone and boundaries are a load-bearing feature, not
polish.

### V. Test-First & Green CI Gate (NON-NEGOTIABLE)

- Every behavior change ships with tests in the same change. New behavior is
  specified by a failing test before it is implemented.
- The full unit-test suite MUST stay green (CI `tests.yml`). The current baseline
  is 353 tests; that number only moves up.
- Any change touching the knowledge base MUST pass `kb-validate` and keep
  `eval-rag` hit@3 ≥ 0.95 (CI `kb-quality.yml`).
- Every new write-capable tool MUST use strict JSON-boundary argument validation
  (`services/arguments.py`): no Python truthiness, no `int(...)`/`bool(...)`
  coercion, no lenient enum matching across the tool boundary.
- Invariants from Principles I–III MUST be protected by guard tests (e.g.
  `test_memory_guard.py`, `test_safety_router.py`, `test_decision_log_citations.py`).

**Rationale:** This is a brownfield system that has already paid down phantom-doc
and coercion debt; regressions here are how that debt comes back.

### VI. Traceable Decisions, No Phantom Docs

- Every architectural decision is recorded as an ADR and/or a `DECISION_LOG.md`
  entry. Any code or spec citation of a `Dxxx` id or `ADR §` reference MUST
  resolve to an in-repo file (guarded by `test_decision_log_citations.py`). (AGENTS.md)
- Division of governance documents is fixed and MUST NOT be duplicated:
  - `docs/adr/` + `docs/DECISION_LOG.md` record **why** (architectural decisions).
  - `specs/<feature>/` (spec.md / plan.md / tasks.md) record **what + how + tasks**
    for each feature.
  - This constitution records the **non-negotiable rules** that bind both.
  These artifacts cross-link; they do not restate each other.

**Rationale:** The project nearly drifted on un-committed "ghost" design docs; a
single resolvable source of truth is now an enforced invariant.

### VII. Hermes Boundary & Data Privacy

- Hermes CLI is the main shell. The project MUST NOT build a competing general
  chat engine, and MUST NOT modify the `~/.hermes/hermes-agent` install tree.
- CGM capability is exposed to Hermes only through tool / storage / memory-provider
  adapters; future memory providers are user plugins or project services, never
  in-tree Hermes providers.
- PHI columns MUST be application-encrypted at rest (Fernet); the DB file and key
  file MUST be created with `0600` permissions on Unix-like systems.
- Secrets and tokens (e.g. Dexcom credentials) MUST NOT appear in audit payloads,
  logs, or report content.

**Rationale:** The replaceable-engine boundary keeps the project testable and
portable; the privacy rules are baseline obligations for a health dataset.

## Architecture & Technology Baseline

- Language/runtime: Python ≥ 3.11; data models use Pydantic v2; persistence is
  local SQLite at the single resolved DB path (one source of truth for CLI and
  both Hermes plugins).
- Memory model is three-tier Hot / Warm / Cold (D029): Hot (recent glucose +
  events + L2 profile + active L3 hypotheses) is read directly from SQLite with no
  retrieval layer; Warm is background-synthesized state injected at prefetch; Cold
  (L1 episodes + medical cards) is retrieved on demand.
- Default authoritative retrieval is sparse CJK-aware BM25 over verified-schema
  claim cards; dense/semantic retrieval is opt-in only (D030, D035, D036).
- `docs/adr/ADR-0001` is the standing architectural baseline. A change that
  contradicts it requires a superseding ADR, not a silent deviation.

## Development Workflow & Quality Gates

- Feature work follows the Spec-Driven cycle:
  `constitution → specify → (clarify) → plan → (checklist) → tasks → analyze → implement`.
  Every `plan.md` MUST include a Constitution Check that explicitly maps the
  feature against Principles I–VII; an unjustified violation blocks the plan.
- The Hermes persona workflow maps onto these phases and remains in force:
  Caesar (architecture/spec & plan), Apollo (spec review & this constitution),
  Damocles (security review; owns Principles I–III & VII sign-off), Luna
  (persona/design; owns Principle IV), Ark (implementation), QA (verification &
  `analyze`). Damocles retains veto on HIGH/CRITICAL risk without mitigation.
- Conflict tiebreaker, in priority order:
  **Security > Functionality > Aesthetics > Performance > Developer Convenience.**
- No feature is "done" until its tests pass locally and in CI, its spec/plan/tasks
  are consistent (`analyze` clean), and any new decision is logged.

## Governance

- This constitution supersedes ad-hoc practices and competing planning documents.
  When an older doc conflicts with it, the constitution wins and the older doc is
  reconciled or retired.
- Amendments require: (a) a `DECISION_LOG.md` entry stating the change and its
  rationale, (b) a semantic version bump per the rules below, and (c) an updated
  Sync Impact Report at the top of this file.
- Versioning policy:
  - MAJOR: removing/redefining a principle or a backward-incompatible governance change.
  - MINOR: adding a principle or materially expanding guidance.
  - PATCH: clarifications and wording with no semantic change.
- Compliance review: every PR/merge and every `plan.md` Constitution Check MUST
  verify compliance. Complexity that appears to violate a principle MUST be
  justified in writing or removed.
- Runtime development guidance (commands, structure, conventions) lives in
  `README.md` and `AGENTS.md`; this constitution governs the non-negotiable rules.

**Version**: 1.0.0 | **Ratified**: 2026-06-08 | **Last Amended**: 2026-06-08
