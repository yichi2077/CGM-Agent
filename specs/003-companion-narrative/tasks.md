# Tasks: Companion Narrative + Negotiated Interaction (F4)

**Input**: Design documents from `/specs/003-companion-narrative/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Constitution Principle V (NON-NEGOTIABLE) — test-first for all new behaviors.

**Organization**: Tasks grouped by user story (US1=narrative, US2=hypothesis, US3=escalation).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)

## Path Conventions

- Source: `src/hermes_cgm_agent/`
- Tests: `tests/`

---

## Phase 1: Setup

**Purpose**: Prepare test infrastructure and read existing code patterns.

- [ ] T001 Read existing builder.py narrative patterns (`_daily_card_text`, `_overview_section`, `_metrics_section`, `_observations_section`, `_follow_up_section`, `_patterns_section`, `_doctor_appendix_section`) and document current audience-branching behavior in a test helper
- [ ] T002 Create test fixture factory for GlucoseAggregate with configurable TIR/TAR/TBR/MBG/CV/GMI/coverage values in tests/test_narrative_templates.py
- [ ] T003 [P] Create test fixture factory for L3Hypothesis with configurable state and evidence_count in tests/test_hypothesis_narrative.py
- [ ] T004 [P] Create test fixture factory for consecutive anomaly day simulation in tests/test_escalation_concern.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 Add `_tir_life_language(tir: float, audience: ReportAudience) -> str` helper in builder.py that translates TIR percentage to life-language for SELF/FAMILY audiences
- [ ] T006 [P] Add `_tar_tbr_life_language(tar: float, tbr: float, audience: ReportAudience) -> str` helper in builder.py that translates TAR/TBR to life-language
- [ ] T007 Verify existing test suite passes: `python -m unittest discover -s tests` → 374+ green

**Checkpoint**: Translation helpers available; all user story work can begin.

---

## Phase 3: User Story 1 — Report Narrative Quality (Priority: P1) 🎯 MVP

**Goal**: Every report section uses persona-compliant conversational Chinese for SELF, simplest language for FAMILY, and clinical structure for CLINICIAN.

**Independent Test**: Generate reports for each audience with known data and verify narrative quality, length norms, and absence of clinical jargon.

### Tests for User Story 1 ⚠️ (test-first)

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T008 [P] [US1] Test daily card SELF: TIR=75% → content contains "范围里" or equivalent life-language, NOT "TIR 75%"; length ≤50 chars — in tests/test_narrative_templates.py
- [ ] T009 [P] [US1] Test daily card FAMILY: any scenario → content is ≤1 sentence, contains no digits, contains no "TIR"/"TAR"/"TBR" — in tests/test_narrative_templates.py
- [ ] T010 [P] [US1] Test daily card CLINICIAN: TIR=75% → content contains "TIR 75%" (raw numbers preserved) — in tests/test_narrative_templates.py
- [ ] T011 [P] [US1] Test overview section SELF: no data → content uses gentle life-language, not clinical jargon — in tests/test_narrative_templates.py
- [ ] T012 [P] [US1] Test overview section FAMILY: no data → content is reassuring, ≤80 chars — in tests/test_narrative_templates.py
- [ ] T013 [P] [US1] Test metrics section SELF: TIR=75%, TAR=20%, TBR=5% → content uses "偏高的时候" not "TAR 20%" — in tests/test_narrative_templates.py
- [ ] T014 [P] [US1] Test observations section SELF: TAR>TBR → content uses life-language pattern description — in tests/test_narrative_templates.py
- [ ] T015 [P] [US1] Test follow-up section SELF: unconfirmed events present → content uses gentle invitation language — in tests/test_narrative_templates.py
- [ ] T016 [P] [US1] Test daily card normal (all in range): SELF → content is brief positive message ≤50 chars, not "TIR 100%" — in tests/test_narrative_templates.py

### Implementation for User Story 1

- [ ] T017 [US1] Refactor `_daily_card_text` in builder.py to use life-language translation helpers for SELF/FAMILY audiences — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T018 [US1] Refactor `_overview_section` in builder.py to use conversational Chinese for SELF audience — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T019 [US1] Refactor `_metrics_section` in builder.py to translate TIR/TAR/TBR to life-language for SELF/FAMILY — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T020 [US1] Refactor `_observations_section` in builder.py to use pattern-in-life-terms for SELF audience — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T021 [US1] Refactor `_follow_up_section` in builder.py to use gentle invitation language for SELF — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T022 [US1] Verify all US1 tests pass and existing tests still pass — `python -m unittest discover -s tests`

**Checkpoint**: All report sections use persona-compliant narrative for all three audiences.

---

## Phase 4: User Story 2 — Hypothesis Negotiated Narrative (Priority: P2)

**Goal**: L3 hypotheses in reports use state-appropriate hedged language matching SOUL.md templates.

**Independent Test**: Generate report sections with hypotheses in each of the 4 states and verify narrative matches the expected template.

### Tests for User Story 2 ⚠️ (test-first)

- [ ] T023 [P] [US2] Test hypothesis CANDIDATE narrative: content contains "看起来" or "可能" AND contains invitation ("要不要" / "留意") — in tests/test_hypothesis_narrative.py
- [ ] T024 [P] [US2] Test hypothesis OBSERVING narrative with evidence_count=3: content references count naturally, does NOT assert causation — in tests/test_hypothesis_narrative.py
- [ ] T025 [P] [US2] Test hypothesis STABLE narrative: content contains "比较常见" or "模式" equivalent, still hedged — in tests/test_hypothesis_narrative.py
- [ ] T026 [P] [US2] Test hypothesis ARCHIVED narrative: content contains "最近不明显" or demotion language, NOT "失败" or "错误" — in tests/test_hypothesis_narrative.py
- [ ] T027 [P] [US2] Test hypothesis narrative forbidden patterns: no state produces "经分析发现" / "研究表明" / "数据证明" / "你应该" — in tests/test_hypothesis_narrative.py
- [ ] T028 [P] [US2] Test hypothesis CANDIDATE with evidence_count=0 (edge case): narrative degrades gracefully to "看起来可能有关" — in tests/test_hypothesis_narrative.py

### Implementation for User Story 2

- [ ] T029 [US2] Add `_hypothesis_narrative(self, state: HypothesisState, evidence_count: int, audience: ReportAudience) -> str` method in builder.py — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T030 [US2] Integrate `_hypothesis_narrative` into `_patterns_section` to use state-aware language instead of generic pattern summaries — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T031 [US2] Add CLINICIAN audience handling for hypothesis narrative (structured evidence summary, no hedged language) — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T032 [US2] Verify all US2 tests pass and existing tests still pass — `python -m unittest discover -s tests`

**Checkpoint**: Hypothesis narratives are state-aware and persona-compliant.

---

## Phase 5: User Story 3 — Escalation Concern Strategy (Priority: P3)

**Goal**: Report narrative escalates concern based on consecutive anomaly days; vulnerable populations get earlier escalation.

**Independent Test**: Simulate 1-7 consecutive anomaly days and verify correct escalation language at each threshold.

### Tests for User Story 3 ⚠️ (test-first)

- [ ] T033 [P] [US3] Test escalation NORMAL (day 1): report uses standard data attribution, no concern language — in tests/test_escalation_concern.py
- [ ] T034 [P] [US3] Test escalation CONCERN (day 3): report content contains personal concern ("你还好吗" or equivalent) — in tests/test_escalation_concern.py
- [ ] T035 [P] [US3] Test escalation EXTERNAL_SUPPORT (day 5): report content contains gentle external support suggestion ("跟医生聊聊" or equivalent) — in tests/test_escalation_concern.py
- [ ] T036 [P] [US3] Test vulnerable population escalation: day 3 concern language is extra gentle compared to standard — in tests/test_escalation_concern.py
- [ ] T037 [P] [US3] Test escalation forbidden patterns: no level produces "警告" / "警报" / "危险" / "你应该去看医生" — in tests/test_escalation_concern.py
- [ ] T038 [P] [US3] Test red-zone suppression: when safety_decision is red_zone, no escalation concern narrative appears — in tests/test_escalation_concern.py
- [ ] T039 [P] [US3] Test escalation with missing vulnerable flag: falls back to standard timeline without error — in tests/test_escalation_concern.py

### Implementation for User Story 3

- [ ] T040 [US3] Add `consecutive_anomaly_days(user_id: str, now: datetime) -> int` method to `PushSchedulerService` in scheduler.py (class verified to exist at `scheduler.py:70`) — in src/hermes_cgm_agent/services/scheduling/scheduler.py
- [ ] T041 [US3] Add `_read_vulnerable_flag(user_id: str) -> bool` helper that reads L2ProfileItem `vulnerable_population` key — in src/hermes_cgm_agent/services/scheduling/scheduler.py. NOTE (analyze A1): no upstream currently writes this key, so the vulnerable path is dormant in production and exercised only via test fixtures — see plan.md KNOWN GAP.
- [ ] T042 [US3] Include `escalation_level` and `consecutive_anomaly_days` in push_tick result dict — in src/hermes_cgm_agent/services/scheduling/scheduler.py
- [ ] T042b [US3] **Wire escalation into on-demand reports (analyze D1)**: `builder.generate()` only receives a `ReportInput`, so the push-path escalation never reaches `reports.generate`. Add optional `consecutive_anomaly_days: int | None` and `escalation_level: str | None` fields to `ReportInput` (domain/report.py), and have BOTH the push path AND the `reports.generate` executor populate them by calling `PushSchedulerService.consecutive_anomaly_days(...)` + the vulnerable-flag helper before building the report. Add a test that an on-demand report at day 3 renders concern language. — in src/hermes_cgm_agent/domain/report.py, src/hermes_cgm_agent/services/tools/handlers/reports.py
- [ ] T043 [US3] Add `_escalation_concern_narrative(self, consecutive_days: int, is_vulnerable: bool, audience: ReportAudience) -> str` method in builder.py; read `consecutive_days`/`escalation_level` from `ReportInput` (populated per T042b) — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T044 [US3] Integrate escalation concern into `_follow_up_section` (the pinned target section) in builder.py when `consecutive_days >= 3` — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T045 [US3] Ensure red-zone override suppresses escalation concern narrative (verify existing behavior preserved) — in src/hermes_cgm_agent/services/reports/builder.py
- [ ] T045b [US3] Regression test (analyze G1 / FR-011): assert narrative refactor preserves `evidence_refs`, `source_tracks`, `confidence`, and `data_quality_warnings` on report sections (narrative is a rendering concern only) — in tests/test_report_builder.py
- [ ] T046 [US3] Verify all US3 tests pass and existing tests still pass — `python -m unittest discover -s tests`

**Checkpoint**: Escalation concern strategy is implemented for standard and vulnerable populations.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: DECISION_LOG, DSG-### review notes, final validation.

- [ ] T047 Add DECISION_LOG entry for F4 decisions: escalation derivation (not persisted), narrative template location (builder.py internal), vulnerable population detection (L2 key) — in docs/DECISION_LOG.md
- [ ] T048 Run full test suite and verify 374+ baseline + F4 new tests (≥15) all pass — `python -m unittest discover -s tests`
- [ ] T049 Run quickstart.md validation scenarios — verify SC-001 through SC-006
- [ ] T050 Review all new narrative templates against SOUL.md §交互原则 and §我们不这样说 table — manual Luna review gate (DSG-001 through DSG-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **User Stories (Phase 3-5)**: All depend on Foundational phase completion
  - US1, US2, US3 can proceed in parallel after Phase 2
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) — No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) — Independent of US1 (different builder methods)
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) — Touches scheduler.py (US1/US2 don't), touches different builder methods

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Helper methods before section methods
- Section methods before integration

### Parallel Opportunities

- US1 tests (T008-T016) can all run in parallel
- US2 tests (T023-T028) can all run in parallel
- US3 tests (T033-T039) can all run in parallel
- US1, US2, US3 implementation can proceed in parallel (different builder methods + scheduler.py)

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T004)
2. Complete Phase 2: Foundational (T005-T007)
3. Complete Phase 3: US1 (T008-T022)
4. **STOP and VALIDATE**: Test all report sections use persona-compliant narrative
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → narrative helpers ready
2. Add US1 → All report sections persona-compliant → Deploy/Demo (MVP!)
3. Add US2 → Hypothesis narratives state-aware → Deploy/Demo
4. Add US3 → Escalation concern strategy → Deploy/Demo
5. Polish → DECISION_LOG + DSG review + final validation

---

## Notes

- All tasks follow test-first (Constitution Principle V)
- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- builder.py is 980 lines; F4 adds ~200 lines. G1 (builder.py拆分) is tracked separately.
- DSG-### review (T050) is a design quality gate, not a code review gate
