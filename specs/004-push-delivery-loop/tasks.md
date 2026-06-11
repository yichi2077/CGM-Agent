---
description: "Task list for F5 — Push Delivery Loop"
---

# Tasks: Push Delivery Loop (F5)

**Input**: Design documents from `specs/004-push-delivery-loop/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: REQUIRED — Constitution Principle V (test-first, green CI) + FR-014 mandate regression coverage. Test tasks are written first and must FAIL before implementation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 (Setup, Foundational, Polish carry no story label)
- All paths are repo-relative.

---

## Phase 1: Setup

- [X] T001 Record the current green test baseline (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`) and note the count in `specs/004-push-delivery-loop/plan.md` (Notes) — guards SC-006 (no regressions). Expected: ≥407 tests (post-F4 baseline).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Register push_tick as a tool and wire it through the handler/dispatch/plugin layers. This is the D1 plumbing that makes push_tick invocable.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

### Tests (write first, must FAIL)

- [X] T002 Write FAILING tests in `tests/test_push_tick_tool.py`: (a) the tool is in the active registry (`build_default_tool_registry()`) with name **`scheduling.push_tick`** (dotted `group.action` form, matching the convention of every other tool — `delivery.send`, `data.dexcom_sync`; analyze N1), group "scheduling", status "active"; (b) input schema requires `user_id`, accepts optional `now`, has no `$ref`; (c) output schema includes `pushed` and `silent_consent`. (C1, FR-001)

- [X] T003 Write FAILING test in `tests/test_tool_registry.py` (`ExecutorDispatchCoverageTests`): verify `scheduling.push_tick` is in `ToolExecutor._DISPATCH` and maps to a callable method. This test will initially fail because the tool is not yet registered. (FR-012)

- [X] T004 Write FAILING test in `tests/test_hermes_plugin_integration.py`: verify `cgm_scheduling_push_tick` (= `cgm_` + `scheduling.push_tick`.replace(".","_")) appears in the runtime registration set and in `plugin.yaml` `provides_tools`. (FR-003, FR-012)

### Implementation

- [X] T005 Create `src/hermes_cgm_agent/services/tools/handlers/push_tick.py`: implement `PushTickHandlerMixin` with `_push_tick()` method that builds a `PushSchedulerService` from `self.repository.store` + `self.audit_service`, calls `push_tick(user_id=..., now=...)`, and returns `ToolExecutionResponse` with the result dict. Validate `user_id` (required string) and `now` (optional ISO-8601, parsed to datetime). (FR-001, FR-002, R2)

- [X] T006 Add `PushTickHandlerMixin` import to `src/hermes_cgm_agent/services/tools/handlers/__init__.py`: add import line + `__all__` entry. (FR-003, blast radius — pure append)

- [X] T007 Add `PushTickHandlerMixin` to `ToolExecutor` inheritance in `src/hermes_cgm_agent/services/tools/executor.py`: add to class bases and add `"scheduling.push_tick": "_push_tick"` to `_DISPATCH` dict (dispatch key = registry name; handler method stays `_push_tick`). (FR-003, blast radius — pure append)

- [X] T008 Register the ToolSpec in `build_default_tool_registry()` in `src/hermes_cgm_agent/services/tools/registry.py`: **name="scheduling.push_tick"** (dotted convention; analyze N1), group="scheduling", owner_module="push_scheduler", status="active", risk_level="write", input_schema with user_id (required) + now (optional datetime), output_schema wrapping PushTickResult fields. (FR-001, blast radius — pure append at end of function)

- [X] T009 Add `- cgm_scheduling_push_tick` to `provides_tools` in `integrations/hermes/cgm/plugin.yaml`. (FR-003, blast radius — pure append)

**Checkpoint**: T002–T004 tests now pass. Guard tests (ExecutorDispatchCoverageTests, plugin.yaml drift) pass. push_tick is a fully wired tool. Quickstart V1 + V2 pass.

---

## Phase 3: User Story 1 — push_tick 可被 Hermes cron 调度 (Priority: P1) 🎯 MVP

**Goal**: push_tick is invocable from Hermes cron; returns valid PushTickResult; idempotent across repeated calls for same period.

**Independent Test**: Call `push_tick` with a user_id → verify pushed tiers and silent-consent advancements. Call again → idempotent. (quickstart V2)

### Tests (write first, must FAIL)

- [X] T010 [P] [US1] Extend `tests/test_push_tick_tool.py` with integration tests: (a) invoke push_tick via `ToolExecutor.execute()` with a seeded user → verify `PushTickResult` shape (`status="ok"`, `pushed` is a list, `silent_consent` is a list); (b) invoke twice for same (user, tier, period) → second call returns empty `pushed` for that tier (idempotency); (c) invoke with `now` override → verify the override time is used. (SC-002, C1)

- [X] T011 [P] [US1] Write FAILING test in `tests/test_push_tick_tool.py`: verify silent-consent advancement — seed a `candidate` hypothesis older than `silence_days`, invoke push_tick, verify the hypothesis advanced to `observing` and an audit log entry was created. (spec US1 acceptance scenario 2)

### Implementation

- [X] T012 [US1] Verify `PushSchedulerService` integration works end-to-end through the tool handler: ensure the handler correctly passes `user_id` and `now` to the service and maps the `PushTickResult.to_dict()` to the tool response envelope (status, evidence_refs, audit_id). Debug any wiring issues in `src/hermes_cgm_agent/services/tools/handlers/push_tick.py`. (FR-002, FR-004)

- [X] T013 [US1] Add a DECISION_LOG entry for push_tick toolization + Hermes cron boundary in `docs/DECISION_LOG.md`. (Principle VI)

**Checkpoint**: quickstart V1 + V2 pass. push_tick is invocable, idempotent, audited. Silent-consent works through the tool layer.

---

## Phase 4: User Story 2 — webhook 投递闭环 (Priority: P2)

**Goal**: `delivery.send` with `channel=webhook` makes an HTTP POST to the configured endpoint with a PHI-filtered payload. Failure modes are handled and audited.

**Independent Test**: Call `delivery.send` with `channel=webhook` → HTTP POST made, `delivery_status=sent` on 2xx. No PHI in request body. Missing env → failed, no request. (quickstart V3–V5)

### Tests (write first, must FAIL)

- [X] T014 [P] [US2] Write FAILING tests in `tests/test_webhook_delivery.py`: (a) `WebhookDeliveryTests` — mock HTTP to return 200, verify `delivery_status="sent"`, verify HTTP POST was made to `CGM_WEBHOOK_URL`, verify request body is valid JSON with correct Content-Type; (b) `WebhookFailureTests` — no `CGM_WEBHOOK_URL` env → `delivery_status="failed"`, no HTTP call; HTTP 500 → `delivery_status="failed"`; timeout → `delivery_status="failed"`; invalid URL → `delivery_status="failed"`. (SC-003, SC-005, C2, C5)

- [X] T015 [P] [US2] Write FAILING tests in `tests/test_webhook_delivery.py` (`PHIFilterTests`): (a) filtered payload contains only allowed keys (delivery_id, push_id, tier, period_key, metrics subset, event_summaries subset, delivered_at); (b) injected `user_id`, `content`, `points`, `session_id` are stripped; (c) nested `event_summaries` items only have `type` + `count`. (SC-004, C3)

- [X] T016 [P] [US2] Write FAILING tests in `tests/test_webhook_delivery.py` (`WebhookAuditTests`): audit log contains `delivery_url_domain` (domain only, not full URL), `http_status_code` on success, `error_type` on failure. No full URL, no request body, no response body, no PHI in audit. (C4)

### Implementation

- [X] T017 [US2] Implement webhook HTTP POST in `src/hermes_cgm_agent/services/tools/handlers/delivery.py`: in the existing `_delivery_send` method, replace the `else: delivery_status = "queued"` branch for `channel == "webhook"` with: (1) read `CGM_WEBHOOK_URL` from `os.environ`; (2) if unset, return failed with message; (3) **require `https://` scheme — reject `http://`/other schemes with `failed` + clear message (analyze S1; aggregate health metrics must not go cleartext)**; (4) build delivery manifest dict using `payload_ref` as the `push_id` and extracting `tier` from arguments (analyze U4); (5) apply PHI allowlist filter; (6) `urllib.request.urlopen` POST with 10s timeout, using an opener that **does NOT follow redirects** (a 30x must NOT divert the payload to another host — analyze S1); (7) 2xx → sent, else → failed. (FR-005, FR-008, FR-009, FR-011, R3)
- [X] T017b [US2] Add FAILING security tests in `tests/test_webhook_delivery.py` (analyze S1): (a) `CGM_WEBHOOK_URL=http://…` → `failed`, no request made; (b) endpoint returns a 302 redirect → NOT followed, treated as `failed` (no POST to the redirect target). (FR-011, SC-005)

- [X] T018 [US2] Implement PHI allowlist filter as a standalone function `_filter_webhook_payload(manifest: dict) -> dict` in `src/hermes_cgm_agent/services/tools/handlers/delivery.py`: hard-coded allowlist of permitted keys/paths matching exactly the list in `plan.md` §"PHI Protection" (`delivery_id`, `push_id`, `tier`, `period_key`, `metrics.tir_pct`, `metrics.mean_mgdl`, `metrics.gmi`, `event_summaries[].type`, `event_summaries[].count`, `delivered_at`) (analyze U3); deny-by-default; strip nested objects to allowed subsets. (FR-006, FR-007, C3, R4)

- [X] T019 [US2] Implement audit logging for webhook delivery in `src/hermes_cgm_agent/services/tools/handlers/delivery.py`: parse `delivery_url_domain` from URL via `urllib.parse.urlparse`; log `http_status_code` on success, `error_type` on failure; ensure no PHI, no full URL, no raw payload in audit. (FR-010, C4, R6)

**Checkpoint**: quickstart V3–V5 pass. Webhook delivery works end-to-end. PHI filter verified. Failure modes handled. Audit logs are clean.

---

## Phase 5: Polish & Cross-Cutting

- [X] T019b [P] [US1] Add an empty-window push_tick test (analyze L1): `push_tick` for a user with no CGM data → runs without error, records the push with whatever content `ConsolidationService` produces for an empty window. — in tests/test_push_tick_tool.py
- [X] T019c [P] Add an operator-facing cron-registration doc (analyze G1 / FR-004): in `README.md` (or `AGENTS.md`), show the Hermes cron entry that invokes the `cgm_scheduling_push_tick` tool on a daily schedule. The capability layer owns policy/content/state; Hermes cron owns the cadence.
- [X] T020 [P] Update `docs/DECISION_LOG.md` with entry for webhook delivery channel + PHI allowlist policy + https/no-redirect hardening (Principle VI, analyze S1). (May be combined with T013 if done in same PR.)

- [X] T021 [P] Update `specs/004-push-delivery-loop/plan.md` Notes section with final test count after F5 implementation.

- [X] T022 Run the full suite (`PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests`); confirm green ≥407 baseline from T001 (SC-006). Record count in plan.md Notes.

- [X] T023 Run quickstart V1–V7 end-to-end; confirm Constitution Check (plan.md) still holds. Verify blast-radius guard tests pass (ExecutorDispatchCoverageTests, plugin.yaml drift guard, plugin integration tests).

---

## Dependencies & Execution Order

- **Setup (T001)** → no deps.
- **Foundational (T002–T009)** → blocks all stories. T002–T004 (tests) before T005–T009 (implementation).
- **US1 (T010–T013)** → depends on Foundational. T010/T011 (tests) before T012 (implementation). T013 (DECISION_LOG) parallel.
- **US2 (T014–T019)** → depends on Foundational. T014–T016 (tests) before T017–T019 (implementation). Can proceed in parallel with US1 (different files).
- **Polish (T020–T023)** → after the stories you intend to ship; T022/T023 last.

### Within each story

- Tests first and FAILING, then implementation.
- T017/T018/T019 all modify `delivery.py` — must be sequential (T017 first, then T018, then T019 or combined).
- T005–T009 modify different files from each other and from T017–T019 (except T007 modifies executor.py, which T017 does not).

### Parallel opportunities

- T002 ∥ T003 ∥ T004 (different test files).
- T005 ∥ T006 ∥ T008 ∥ T009 (different files). T007 modifies executor.py (unique).
- After Foundational: US1 (T010–T013) ∥ US2 (T014–T019) — different file sets (push_tick.py vs delivery.py).
- T010 ∥ T011 (same test file but different test classes — can be combined).
- T014 ∥ T015 ∥ T016 (same test file but different test classes — can be combined).
- T020 ∥ T021 (different files).

---

## Implementation Strategy

- **MVP = US1 (push_tick tool)**: Complete Setup + Foundational + US1. Stop and validate (V1, V2). push_tick is now invocable from Hermes cron.
- **Then US2 (webhook delivery)**: Complete US2. Stop and validate (V3–V5). Delivery loop is closed.
- **Then Polish**: DECISION_LOG, full suite, quickstart V1–V7.
- Commit after each phase (or logical group); keep the full suite green throughout.

**Totals**: 27 tasks — Setup 1 · Foundational 8 (3 tests + 5 impl) · US1 5 (T010–T013 + T019b) · US2 7 (T014–T019 + T017b) · Polish 6 (T019c/T020/T021/T022/T023 + …). (T017b/T019b/T019c added in review remediation 2026-06-09.)

**Blast radius shared-file changes** (all pure appends; tool name = `scheduling.push_tick`, external `cgm_scheduling_push_tick` — analyze N1):
- `registry.py`: 1 ToolSpec registration (T008)
- `executor.py`: 1 class base + 1 dispatch entry (T007)
- `handlers/__init__.py`: 1 import + 1 __all__ entry (T006)
- `plugin.yaml`: 1 provides_tools line (T009)
