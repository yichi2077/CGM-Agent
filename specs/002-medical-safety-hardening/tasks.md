---
description: "Task list for F3 — Medical Safety Hardening"
---

# Tasks: Medical Safety Hardening (F3)

**Input**: Design documents from `specs/002-medical-safety-hardening/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md, sec-audit.md
**Tests**: REQUIRED — Constitution Principle V (test-first, green CI) + FR-012 mandate regression coverage. Test tasks are written first and must FAIL before implementation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 / US4 (Setup, Foundational, Polish carry no story label)
- All paths are repo-relative.

---

## Phase 1: Setup

- [ ] T001 Record the current green test baseline (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`) and note the count in `specs/002-medical-safety-hardening/plan.md` (Notes) — guards SC-005 (no regressions). Current expected: 374.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Strengthen `assert_kb_readonly` so it catches `approve` (and any new mutator), then explicitly allowlist `approve` — enabling B2 while *tightening* Principle I. This is the prerequisite that makes US2 (kb.approve) implementable.

**⚠️ CRITICAL**: no user-story work begins until this phase is complete.

**Baseline (analyze I1, verified against code)**: the current `assert_kb_readonly(rag_service)` is a **denylist** over a fixed set `{add, write, insert, upsert, update, delete, save}` — it has **no** `allow_methods` parameter and **does NOT currently block `approve`**. The prior draft's premise ("rejects ANY mutator") was wrong; adding `approve` would silently bypass the guard. F3 therefore both strengthens AND allowlists.

- [ ] T002 Write FAILING tests in `tests/test_memory_guard.py`: (a) `assert_kb_readonly` still blocks the existing mutators `add`/`write`/`insert`/`upsert`/`update`/`delete`/`save`; (b) **NEW** — `assert_kb_readonly` (no allowlist) now ALSO rejects a mutator named `approve` (closing the denylist gap); (c) `assert_kb_readonly(rag_service, allow_methods={"approve"})` permits an `approve` method while still blocking the others. (R4, Principle I)
- [ ] T003 Update `assert_kb_readonly` in `src/hermes_cgm_agent/services/safety/memory_guard.py`: (1) add `approve` to the blocked mutator set so a write method can never silently bypass the guard; (2) add optional `allow_methods: set[str] = frozenset()` param — methods in the set are exempted. Default (no allowlist) blocks `approve` too. Keep the existing `MemoryTrackViolation` error. (R4, FR-013; analyze I1)
- [ ] T004 Update `AuthoritativeRAGService.__init__` in `src/hermes_cgm_agent/services/rag/authoritative.py` to call `assert_kb_readonly(self, allow_methods={"approve"})` — exempting the new `approve` method while blocking all other writes. (R4, G1)

**Checkpoint**: `assert_kb_readonly` tests pass; KB read-only invariant *tightened* (`approve` now caught by default); the single sanctioned `approve` write path is explicitly allowlisted.

---

## Phase 3: User Story 1 — Medical numbers always have authoritative backing (Priority: P1) 🎯 MVP

**Goal**: The citation guard is wired as a mandatory, non-bypassable gate in the report pipeline. Strict mode is default. Unbacked numbers block report delivery.

**Independent Test**: Run `test_citation_guard.py` + `test_report_pipeline.py` → strict blocking works end-to-end (quickstart V1 + V2).

### Tests (write first, must FAIL)

- [ ] T005 [P] [US1] FAILING tests in `tests/test_citation_guard.py`: (a) backed numbers → `ok=true`; (b) unbacked numbers + strict → `ok=false` with violations; (c) unbacked numbers + warn → `ok=true` with violations logged; (d) empty text → `ok=true`; (e) no verified cards in KB → all numbers unbacked → `ok=false` in strict; (f) mixed backed/unbacked → `ok=false` in strict. (C1, SC-001)
- [ ] T006 [P] [US1] FAILING tests in `tests/test_report_pipeline.py`: (a) report with backed numbers → delivered; (b) report with unbacked numbers → blocked, returns "cannot confirm" response; (c) audit log records the violation with correct fields (no leaked content); (d) the "cannot confirm" response follows persona tone. (C4, SC-001)

### Implementation

- [ ] T007 [US1] Do NOT change the function default of `assert_authoritative_quotes` (keep `strict=False`) — the real signature is `assert_authoritative_quotes(documents, generated_text, *, strict=False)` and the existing `rag.verify_quotes` tool + `test_rag` rely on warn-default behaviour. Instead, force `strict=True` only at the report-pipeline integration point (T008). Add a short module docstring note that strict is mandatory at the report gate. (R1, FR-001; analyze N1)
- [ ] T008 [US1] Wire the citation guard as a mandatory gate in the report generation pipeline in `src/hermes_cgm_agent/services/reports/builder.py`: before delivering any report, call `assert_authoritative_quotes(documents, generated_text, strict=True)` — **note positional order: `documents` FIRST, then `generated_text`** (analyze C1; the prior draft had them swapped, which would silently no-op the guard). The `documents` arg is the retrieved authoritative cards used for the report's medical-claim narrative (see T008b for the backing-set scope). On failure, return the standardized "cannot confirm" response and log the violation. (R1, C4, FR-001/002)
- [ ] T008b [US1] Scope the citation guard correctly (analyze I2/I3): (a) run the guard over the report's medical-guidance/claim narrative, NOT over the user's own deterministic metric values (TIR/TAR/mean from `CGMAnalyticsService` are Principle-I-clean by construction and must not be flagged as "unbacked"); (b) the backing set is the retrieved authoritative cards. Because B2 leaves cards unverified this cycle (KNOWN GAP), matching is against retrieved cards regardless of `verified`, while unverified cards still carry the `[待核验]` marker (T009b/FR-006). Tightening the backing set to `verified=true` only is DEFERRED until clinical sign-off exists (otherwise it would block all numeric narrative). Add tests for both (a) and (b). (R1, C4; analyze I2/I3)
- [ ] T009 [US1] Define the "cannot confirm" response template in `src/hermes_cgm_agent/services/reports/renderer.py`: `"这个问题涉及的医学数据我无法确认准确性。我可以帮你整理原始数据，复诊时带给医生。需要我生成数据摘要吗？"` — persona-aligned, gentle, offers alternative. (Principle IV)

**Checkpoint**: quickstart V1 + V2 pass; unbacked numbers blocked; persona-aligned response delivered.

---

## Phase 4: User Story 2 — Knowledge cards have traceable clinical sign-off (Priority: P2)

**Goal**: `kb.approve` tool exists, enforces tier restriction and provenance, and is registered in the tool pipeline. Zero cards auto-approved (KNOWN GAP).

**Independent Test**: Run `test_kb_approve.py` → approve curated card works, auto card rejected, provenance enforced (quickstart V3 + V4).

### Tests (write first, must FAIL)

- [ ] T010 [P] [US2] FAILING tests in `tests/test_kb_approve.py`: (a) approve a curated card → `verified=true`, `reviewer` and `reviewed_at` set; (b) idempotent re-approve same card + same reviewer → no-op, returns current state; (c) approve a `tier=auto` card → rejected with clear message; (d) approve non-existent card → error; (e) missing required `reviewer` → strict validation error; (f) `reviewed_at` defaults to current UTC when omitted; (g) KB validator passes on approved card; (h) KB validator still rejects a card with `verified=true` but no provenance (regression). (C2, SC-002, SC-006)

### Implementation

- [ ] T011 [US2] Add `approve` method to `AuthoritativeRAGService` in `src/hermes_cgm_agent/services/rag/authoritative.py`: accepts `card_id`, `reviewer`, `reviewed_at` (optional, defaults to now); validates card exists and `tier=curated`; sets `verified=true` with provenance; writes updated card to KB JSON; returns updated card dict. (R2, C2, FR-003/005)
- [ ] T012 [US2] Register `kb.approve` tool schema in `src/hermes_cgm_agent/services/tools/registry.py`: name `kb.approve`, arguments `card_id` (required string), `reviewer` (required string), `reviewed_at` (optional string). Strict JSON-boundary validation. (R2, FR-011)
- [ ] T013 [US2] Implement `_kb_approve` handler in `src/hermes_cgm_agent/services/tools/handlers/rag.py`: dispatches to `AuthoritativeRAGService.approve()`, logs audit event with `approval_id`. (R2, C2)
- [ ] T014 [US2] Wire `_kb_approve` dispatch in `src/hermes_cgm_agent/services/tools/executor.py`: add `kb.approve` to the `_DISPATCH` map (routing to `RagHandlerMixin._kb_approve`) AND register the `cgm_kb_approve` tool in `integrations/hermes/cgm/plugin.yaml` `provides_tools` (the drift guard `test_hermes_plugin_integration` will otherwise fail). Use the dotted registry name `kb.approve` for convention consistency. (R2; G1-split dispatch guard)
- [ ] T014b [US2] Surface the unverified marker (analyze G1 / FR-006): ensure retrieval results from `AuthoritativeRAGToolService` tag any `verified=false` card with `[待核验/unverified]` and never present it in an authoritative clinical voice. Add a test asserting an unverified card carries the marker and a verified card does not. (FR-006, US2-AS3)

**Checkpoint**: quickstart V3 + V4 pass; `kb.approve` works end-to-end; tier restriction enforced; provenance recorded; unverified cards carry the `[待核验]` marker.

---

## Phase 5: User Story 3 — Red-zone recovery requires system double-check (Priority: P3)

**Goal**: `SafetyRouter` tracks red-zone timestamps and performs a recovery double-check within the 2-hour window. Both evaluations recorded in `SafetyDecision`.

**Independent Test**: Run `test_safety_router.py` → recovery double-check works, window boundary respected, both evaluations present (quickstart V5).

**Corrected recovery design (analyze D1)**: the prior draft ("record now's red ts, then run a second `evaluate()` on the SAME data within the window") is broken — evaluating the same data twice yields identical original/recovery (cannot detect recovery or relapse) and a literal nested `evaluate()` recurses. The correct model compares the **stored earlier red-zone state** against the **current data on a LATER `evaluate()` call**:
1. On every `evaluate()`, compute the zone result via an internal, non-recursive zone check (e.g. `_evaluate_zone(points, scope)`), NOT by calling `evaluate()` again.
2. If the current result is red zone → store `_last_red_zone[user_id] = (timestamp, zone_result)`.
3. Else, if a stored red-zone entry exists AND `now - stored_ts < RECOVERY_WINDOW_SECONDS` → attach `recovery_check = {active, window_remaining_seconds, original=stored_result, recovery=current_result, recovery_confirmed = current is green/yellow}`.
4. When the window expires, clear the stored entry; `recovery_check=None`.

### Tests (write first, must FAIL)

- [ ] T015 [P] [US3] FAILING tests in `tests/test_safety_router.py` (extend existing): (a) red zone at T0 → later (T0+Δ, within window) green eval → `recovery_check` present with `original`=red, `recovery`=green, `recovery_confirmed=true`; (b) red at T0 → later eval AFTER window expires → `recovery_check` is `None` and stored state cleared; (c) green zone with no prior red → no recovery check; (d) red at T0 → later eval still red (within window) → `recovery_confirmed=false`; (e) `original` always equals the stored T0 red result (NOT a re-eval of current data); (f) window boundary: exactly at `RECOVERY_WINDOW_SECONDS` → no recovery check; (g) `CGM_AGENT_RECOVERY_WINDOW_SECONDS` env override works; (h) the inner re-eval does not recurse into `evaluate()` (no infinite loop / single audit entry). (C3, SC-003; analyze D1)

### Implementation

- [ ] T016 [US3] Add `RECOVERY_WINDOW_SECONDS = 7200` constant and env override `CGM_AGENT_RECOVERY_WINDOW_SECONDS` to `src/hermes_cgm_agent/services/safety/router.py`. (R3, FR-008)
- [ ] T017 [US3] `SafetyRouter` is currently stateless (no `__init__`). Add an `__init__` that initialises `self._last_red_zone: dict[str, tuple[datetime, dict]]`. Extract the zone decision into an internal `_evaluate_zone(...)` helper and have the public `evaluate()` call it (so the recovery re-eval does NOT recurse into `evaluate()`). Implement the corrected recovery logic above: store red-zone state, and on a later in-window non-red eval attach `recovery_check` comparing stored `original` vs current `recovery`. (R3, C3, FR-007; analyze D1/L1)
- [ ] T018 [US3] Add `recovery_check: dict | None = None` optional field to the `SafetyDecision` dataclass in `src/hermes_cgm_agent/services/safety/router.py`. (R3, data-model.md)
- [ ] T018b [US3] Render `recovery_check` into the report header (analyze G2): in `src/hermes_cgm_agent/services/reports/renderer.py`, when `SafetyDecision.recovery_check` is present, surface both the original and recovery evaluations plus a `recovery-confirmed` indicator in the report header (US3-AS3, SC-003 "in its header"). Add a renderer test. (C4, SC-003)

**Checkpoint**: quickstart V5 passes; recovery double-check compares stored original vs current (no recursion); both evaluations rendered in the report header; window boundary respected.

---

## Phase 6: User Story 4 — Security audit (Priority: P2)

**Goal**: `sec-audit.md` exists with SEC-### findings covering OWASP LLM Top 10.

**Independent Test**: Read `sec-audit.md` → covers ≥3 categories, each finding has ID/severity/mitigation (quickstart V6).

### Implementation

- [ ] T019 [P] [US4] Write the Damocles security audit in `specs/002-medical-safety-hardening/sec-audit.md`: cover LLM01 (Prompt Injection), LLM06 (Sensitive Info Disclosure), LLM09 (Excessive Agency) minimum. Each finding: SEC-### ID, severity, description, current mitigation, recommended action. HIGH/CRITICAL findings reference specific code locations. (FR-009/010, SC-004)
- [ ] T020 [P] [US4] Add test assertions in `tests/test_report_pipeline.py` and `tests/test_kb_approve.py` that audit payloads do not contain full claim text, glucose values, or generated narratives. (SEC-003 mitigation, FR-013)
- [ ] T021 [P] [US4] Add test assertion in `tests/test_safety_router.py` that `_last_red_zone_ts` is not included in any serialized `SafetyDecision` output. (SEC-004 mitigation)

**Checkpoint**: quickstart V6 passes; audit covers required categories; audit payload tests pass.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T022 [P] Add a `DECISION_LOG` entry in `docs/DECISION_LOG.md` documenting: (a) citation guard mode change (warn → strict default), (b) recovery window design (in-memory, 2h default), (c) kb.approve allowlist pattern for assert_kb_readonly. (Principle VI)
- [ ] T023 [P] Update F3 status in `docs/BACKLOG.md`: B1=CLOSED, B2=PARTIAL (tooling built, awaiting clinical reviewer), B3=CLOSED. (Principle VI)
- [ ] T024 [P] Note the strict citation guard default and kb.approve tool in `README.md` and/or `AGENTS.md`.
- [ ] T025 Run the full suite (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`); confirm green ≥ 374 baseline from T001 (SC-005).
- [ ] T026 Run quickstart V1–V8 end-to-end; confirm Constitution Check (plan.md) still holds. All 7 principles pass; KNOWN GAP documented.

---

## Dependencies & Execution Order

- **Setup (T001)** → no deps.
- **Foundational (T002–T004)** → blocks US2 (kb.approve needs the allowlist). T002 (tests) before T003–T004.
- **US1 (T005–T009)** → depends on nothing (citation guard is independent of KB approval).
- **US2 (T010–T014)** → depends on Foundational (T002–T004) for the `assert_kb_readonly` allowlist.
- **US3 (T015–T018)** → depends on nothing (SafetyRouter changes are independent).
- **US4 (T019–T021)** → depends on US1 + US2 + US3 being designed (audit references code), but can run in parallel with implementation.
- **Polish (T022–T026)** → after all stories; T025/T026 last.

### Within each story

- Tests first and FAILING, then implementation.
- T013 depends on T011 (approve method) and T012 (schema). T014 depends on T013.

### Parallel opportunities

- US1 (T005–T009) ∥ US3 (T015–T018) — different files, no dependency.
- US4 (T019–T021) can run parallel with US1/US3 implementation.
- After Foundational, US2 can start. US1 and US3 have no Foundational dependency.
- Test-authoring tasks marked [P] within a story run together.

---

## Implementation Strategy

- **MVP = US1** (strict citation guard). Stop and validate (V1/V2) before US2/US3.
- Then US2 (kb.approve), then US3 (recovery check), then US4 (audit). Each is an independently testable increment.
- Commit after each phase; keep the full suite green throughout.

**Totals**: 29 tasks — Setup 1 · Foundational 3 · US1 6 (T005–T009 + T008b) · US2 6 (T010–T014 + T014b) · US3 5 (T015–T018 + T018b) · US4 3 · Polish 5. (T008b/T014b/T018b added in review remediation 2026-06-09.)
