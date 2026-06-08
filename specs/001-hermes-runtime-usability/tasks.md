---
description: "Task list for F1 — Hermes Runtime Usability"
---

# Tasks: Hermes Runtime Usability (F1)

**Input**: Design documents from `specs/001-hermes-runtime-usability/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: REQUIRED — Constitution Principle V (test-first, green CI) + FR-014 mandate regression coverage. Test tasks are written first and must FAIL before implementation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (Setup, Foundational, Polish carry no story label)
- All paths are repo-relative.

---

## Phase 1: Setup

- [x] T001 Record the current green test baseline (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`) and note the count in `specs/001-hermes-runtime-usability/plan.md` (Notes) — guards SC-006 (no regressions).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: single coherent store + key, the prerequisite that makes US1/US2/US3 verifiable end-to-end in Hermes.

**⚠️ CRITICAL**: no user-story work begins until this phase is complete.

- [x] T002 Write FAILING tests in `tests/test_config.py`: (a) `AppConfig.from_env().database_path == resolve_database_path(HERMES_HOME)`; (b) `CGM_AGENT_DB_PATH` override precedence; (c) `storage_key_path` co-located with the DB dir; (d) warning when an explicit key dir ≠ DB dir. (C1)
- [x] T003 Route `AppConfig.from_env()` through `resolve_database_path()` and derive `storage_key_path` from `database_path.parent` in `src/hermes_cgm_agent/config.py` (FR-001/002/003).
- [x] T004 Emit a warning when `CGM_AGENT_STORAGE_KEY_PATH` resolves outside the DB directory in `src/hermes_cgm_agent/config.py` (Damocles INFO).
- [x] T005 [P] Ensure `SQLiteStore` raises an explicit error on decryption failure (never a silent `None`) in `src/hermes_cgm_agent/storage/sqlite.py` (spec Edge Cases).

**Checkpoint**: CLI and both plugins resolve the same store + co-located key; T002 tests pass.

---

## Phase 3: User Story 1 — 我能在 Hermes 里看到自己的数据 (Priority: P1) 🎯 MVP

**Goal**: CLI-imported/seeded data is visible to the agent in Hermes; legacy data migrates safely; empty first-run is guided.

**Independent Test**: seed via CLI → query that user in a Hermes conversation → agent returns metrics from the seeded data (quickstart V1).

### Tests (write first, must FAIL)

- [x] T006 [P] [US1] FAILING migration tests in `tests/test_migrate_legacy_data.py`: DB+key copied together; missing-key → refuse; existing target + no `--force` → refuse; `--dry-run` no-op; `--force` backs up first; no secret bytes printed. (C3)
- [x] T007 [P] [US1] FAILING data-visibility test in `tests/test_hermes_plugin_integration.py`: a store seeded via the CLI path is read back through the plugin executor at the same resolved path. (SC-001)

### Implementation

- [x] T008 [US1] Create `scripts/migrate_legacy_data.py`: copy `.runtime/app.db` + `.runtime/storage.key` to the canonical dir, non-destructive, `--dry-run`/`--force`, refuse on missing key, never print secrets. (R5, C3)
- [x] T009 [US1] Wire a `migrate-db` subcommand to that script in `src/hermes_cgm_agent/cli.py`.
- [x] T010 [US1] Detect a present legacy store at CLI startup and print a migration hint in `src/hermes_cgm_agent/cli.py` (Damocles W4).
- [x] T011 [US1] Default the `seed-demo` DB path to the canonical resolved path in `src/hermes_cgm_agent/cli.py` (FR-001).
- [x] T012 [US1] Add opt-in `--seed-demo` to `hermes-install` in `src/hermes_cgm_agent/cli.py` (FR-012; no auto-seed).
- [x] T013 [US1] Emit a gentle, persona-aligned empty-store prompt via `src/hermes_cgm_agent/services/memory/provider.py` prefetch/system-prompt surface (FR-012, Principle IV).

**Checkpoint**: quickstart V1 + V5 pass; data visible; migration safe and reversible.

---

## Phase 4: User Story 2 — Agent 可靠记录事件 (Priority: P2)

**Goal**: the agent creates events with only `event_type` + `ts_start`; the system fills and forces bookkeeping/provenance.

**Independent Test**: call `events.create` with minimal fields → ok, `created_by=agent`, `user_confirmed=false`, uuid assigned; override attempts are overwritten (quickstart V3).

### Tests (write first, must FAIL)

- [x] T014 [P] [US2] FAILING tests in `tests/test_event_tools.py`: minimal-field create succeeds; model-supplied `created_by:"user"` / `user_confirmed:true` / fake `event_id` are all overwritten; invalid args strictly rejected (no coercion). (C2, Damocles W2)
- [x] T015 [P] [US2] FAILING test in `tests/test_tool_registry.py`: `events.create` (and timeseries/aggregate) schemas contain no unresolved `$ref`. (C2)

### Implementation

- [x] T016 [US2] Flatten the `events.create` input `event` to an inline object (only `event_type`+`ts_start` required, `additionalProperties:false`) in `src/hermes_cgm_agent/services/tools/registry.py` (R2).
- [x] T017 [US2] Resolve/remove the dangling output `$ref` for `timeseries.get_points` / `timeseries.get_aggregate` in `src/hermes_cgm_agent/services/tools/registry.py` (depends on T016 — same file).
- [x] T018 [US2] Force `event_id`/`user_id`/`created_by`/`user_confirmed` (hard overwrite, before validate) in `_create_event` in `src/hermes_cgm_agent/services/tools/executor.py` (R3, FR-007).
- [~] T019 [P] [US2] (Optional ergonomics) Give `UserEvent` technical-field defaults — **SKIPPED**: executor hard-overwrite (T018) fully covers FR-006/007; model-level defaults add domain surface with no behavioral gain.

**Checkpoint**: quickstart V3 passes; provenance unspoofable; schemas self-contained.

---

## Phase 5: User Story 3 — 我掌控 Agent 的记忆 (Priority: P3)

**Goal**: `memory.confirm` / `memory.correct` are invocable from a Hermes conversation, registered exactly once.

**Independent Test**: confirm a pending candidate from a Hermes conversation → promoted to L1 and retrievable; exactly one memory-confirm tool visible (quickstart V4).

### Tests (write first, must FAIL)

- [x] T020 [P] [US3] FAILING/guard test in `tests/test_hermes_plugin_integration.py`: exactly one invocable `memory.confirm` and one `memory.correct` (no duplicate across `cgm` + `cgm_memory`). (C4, Damocles W3)

### Implementation

- [x] T021 [US3] Diagnose whether Hermes surfaces the `cgm_memory` provider's `get_tool_schemas()` tools to the model; record the finding in `specs/001-hermes-runtime-usability/research.md` (R4 step 1).
- [x] T022 [US3] IF unreachable: remove the `memory.confirm/correct` exclusion in `integrations/hermes/cgm/__init__.py` and route through the executor.
- [x] T023 [US3] IF standalone now registers them: make `cgm_memory` `get_tool_schemas()` omit those two to avoid duplicates in `integrations/hermes/cgm_memory/__init__.py` (W3).
- [x] T024 [US3] Sync `integrations/hermes/cgm/plugin.yaml` `provides_tools` with the active registration (drift guard test already exists).

**Checkpoint**: quickstart V4 passes; memory loop closes; single registration.

---

## Phase 6: Polish & Cross-Cutting

- [x] T025 [P] Add a `DECISION_LOG` entry (path-resolution unification + forced event provenance) in `docs/DECISION_LOG.md` (Principle VI).
- [x] T026 [P] Retire `docs/FIX-PLAN-*` (folded into this spec) and update F1 state in `docs/BACKLOG.md` (Principle VI / backlog G4).
- [x] T027 [P] Note the unified store path + `migrate-db` in `README.md`.
- [x] T028 Run the full suite (`unittest discover -s tests`); confirm green ≥ baseline from T001 (SC-006). → **372 OK**.
- [x] T029 Run quickstart V1–V7 end-to-end; confirm Constitution Check (plan.md) still holds. → migrate-db / dev-status / kb-validate smokes pass; V1–V7 covered by the green suite; Constitution 7/7 still holds.

---

## Dependencies & Execution Order

- **Setup (T001)** → no deps.
- **Foundational (T002–T005)** → blocks all stories. T002 (tests) before T003–T005.
- **US1 (T006–T013)**, **US2 (T014–T019)**, **US3 (T020–T024)** → all depend on Foundational; otherwise largely independent (US1 touches `cli.py`/`scripts/`/`provider.py`; US2 touches `registry.py`/`executor.py`/`domain`; US3 touches the plugin adapters).
- **Polish (T025–T029)** → after the stories you intend to ship; T028/T029 last.

### Within each story

- Tests first and FAILING, then implementation.
- T017 depends on T016 (same file). T022/T023 are conditional on T021's diagnosis.

### Parallel opportunities

- T005 ∥ T003/T004 (different files).
- After Foundational, US1 / US2 / US3 can proceed in parallel (different file sets) — this maps to the BACKLOG "Stage 2" worktree-subagent plan, though within F1 it is small enough to run P1→P2→P3 sequentially.
- Test-authoring tasks marked [P] within a story run together.

---

## Implementation Strategy

- **MVP = US1** (data visible + safe migration). Stop and validate (V1/V5) before US2/US3.
- Then US2 (event capture), then US3 (memory loop). Each is an independently testable increment.
- Commit after each phase (or logical group); keep the full suite green throughout.

**Totals**: 29 tasks — Setup 1 · Foundational 4 · US1 8 · US2 6 · US3 5 · Polish 5.
