# Implementation Plan: Push Delivery Loop (F5)

**Branch**: `004-push-delivery-loop` | **Date**: 2026-06-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-push-delivery-loop/spec.md`

## Summary

Close two gaps that turn the CGM agent into a proactive companion:

1. **D1 — push-tick toolization**: Wrap the existing `PushSchedulerService.push_tick()`
   as a registered, schema-validated Hermes tool (`push_tick`) wired into
   `ToolExecutor._DISPATCH` and `plugin.yaml`, so Hermes cron can invoke it on
   schedule. The scheduling core is already complete — this is pure plumbing:
   new handler mixin, registry entry, dispatch entry, plugin declaration.

2. **D2 — webhook delivery**: Implement HTTP POST for the `webhook` channel in
   `delivery.send`. Read endpoint from `CGM_WEBHOOK_URL` env var (model cannot
   redirect). Apply a hard-coded PHI allowlist filter before sending. 10-second
   timeout, no retry. Audit delivery without logging PHI.

F5 has the **largest blast radius** — it touches 4 shared files. The plan
mitigates this by ensuring all changes are pure appends (one new ToolSpec, one
new dispatch entry, one new import, one new plugin.yaml line) that do not
conflict with F3/F4 parallel append operations.

## Technical Context

**Language/Version**: Python ≥ 3.11
**Primary Dependencies**: Pydantic v2, stdlib `urllib.request` (HTTP POST),
stdlib `sqlite3`, `unittest`
**Storage**: local SQLite at canonical path; `push_events` table already exists
**Testing**: `unittest` (`python -m unittest discover -s tests`); CI `tests.yml`
**Target Platform**: local macOS/Linux behind Hermes Agent shell
**Project Type**: single project — CGM capability layer (CLI + Hermes plugins)
**Performance Goals**: push_tick < 2s per user; webhook POST ≤ 10s timeout
**Constraints**: offline-capable core except webhook (requires network); DB+key
`0600`; no secrets/PHI in audit/logs/webhook payload; do not modify
`~/.hermes/hermes-agent` install tree
**Scale/Scope**: single local user; 1–3 push tiers per tick; 1 webhook endpoint

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | Impact of F5 | Verdict |
|---|---|-----------|---------|
| I | Medical zero-tolerance & KB read-only | Push content comes from `ConsolidationService.synthesize_state()` which reuses deterministic analytics; no clinical numbers produced by model. | ✅ Pass |
| II | Dual-track isolation & one-way write | Push scheduling reads from both CGM data and memory but does not merge tracks; `assert_track_isolation` is not bypassed. | ✅ Pass — guard tests must stay green |
| III | Hard-coded safety routing | push_tick does not bypass safety router; red-zone guard is independent of push delivery. | ✅ Pass — no change |
| IV | Informed-companion persona | Push content follows persona tone (ConsolidationService already enforces this). Webhook payload is machine-readable, not user-facing narrative. | ✅ Pass |
| V | Test-first & green CI | New regression tests REQUIRED: push_tick registration/dispatch, webhook delivery, PHI-redaction allowlist, env-var endpoint sourcing, idempotency. | ✅ Pass — enforced via tasks |
| VI | Traceable decisions, no phantom docs | New tool + webhook channel → add DECISION_LOG entry. | ✅ Pass — DECISION_LOG task included |
| VII | Hermes boundary & data privacy | Strengthens it: push_tick is externally driven (Hermes cron), capability layer owns policy/content/state only. Webhook PHI filter prevents leakage. Endpoint URL from env only (model cannot redirect). Audit logs contain no PHI. | ✅ Pass — reinforced |

**Result: PASS — no violations. No Complexity Tracking required.**

## Project Structure

### Documentation (this feature)

```text
specs/004-push-delivery-loop/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (tool wrapping, webhook PHI, HTTP client)
├── data-model.md        # Phase 1 — PushTickResult, DeliveryManifest, WebhookConfig
├── quickstart.md        # Phase 1 — runnable validation mapped to SC-001..006
├── contracts/
│   └── push-tick-and-delivery.md  # Phase 1 — push_tick + webhook delivery contracts
└── tasks.md             # Phase 2 — created by /speckit-tasks (not here)
```

### Source Code (affected real paths)

```text
src/hermes_cgm_agent/
├── services/
│   ├── tools/
│   │   ├── executor.py              # _DISPATCH: add "scheduling.push_tick" → "_push_tick"
│   │   ├── registry.py              # build_default_tool_registry: add scheduling.push_tick ToolSpec
│   │   └── handlers/
│   │       ├── __init__.py          # import PushTickHandlerMixin
│   │       ├── base.py              # (unchanged)
│   │       ├── delivery.py          # _delivery_send: implement webhook HTTP POST + PHI filter
│   │       └── push_tick.py         # NEW — PushTickHandlerMixin
│   └── scheduling/
│       └── scheduler.py             # (unchanged — already complete)
└── domain/                          # (unchanged)

integrations/hermes/cgm/
└── plugin.yaml                      # provides_tools: add cgm_scheduling_push_tick

tests/
├── test_tool_registry.py            # guard: push_tick in active set + dispatch
├── test_hermes_plugin_integration.py # guard: push_tick in plugin registration + drift
├── test_push_tick_tool.py           # NEW — push_tick handler tests
└── test_webhook_delivery.py         # NEW — webhook delivery + PHI filter tests
```

**Structure Decision**: Single-project capability layer. F5 adds one new handler
module (`push_tick.py`) and modifies `delivery.py` for webhook. All shared-file
changes are pure appends. No new package or architectural layer.

## Blast Radius Mitigation

F5 modifies 4 shared files that F3/F4 also touch. The mitigation strategy:

| Shared File | F5 Change | Nature | Conflict Risk |
|-------------|-----------|--------|---------------|
| `registry.py` | Add one `registry.register(ToolSpec(name="scheduling.push_tick", ...))` call at end of `build_default_tool_registry()` | Pure append | LOW — F3/F4 also append at end; merge appends sequentially |
| `executor.py` `_DISPATCH` | Add `"scheduling.push_tick": "_push_tick"` entry | Pure dict entry | LOW — each feature adds its own key |
| `handlers/__init__.py` | Add `from ...push_tick import PushTickHandlerMixin` import + `__all__` entry | Pure append | LOW — each feature adds its own import |
| `plugin.yaml` | Add `- cgm_scheduling_push_tick` to `provides_tools` | Pure list append | LOW — each feature adds its own line |

Guard tests (`ExecutorDispatchCoverageTests`, `plugin.yaml drift guard`) verify
consistency after each merge. If F3/F4 merge first, F5's guard tests catch any
missing wiring. If F5 merges first, F3/F4's guard tests catch theirs.

## Phase 0 — Research

See [research.md](research.md). Resolves: push_tick tool interface design,
webhook PHI allowlist, HTTP client choice, endpoint configuration source,
audit logging for external deliveries.

## Phase 1 — Design & Contracts

- [data-model.md](data-model.md) — PushTickResult (tool output), DeliveryManifest
  (webhook payload), WebhookConfig (env-derived).
- [contracts/push-tick-and-delivery.md](contracts/push-tick-and-delivery.md) —
  `push_tick` tool contract + `delivery.send` webhook contract + PHI allowlist.
- [quickstart.md](quickstart.md) — end-to-end validation scenarios mapped to
  the spec's SC-001..SC-006.

## Damocles Security Review Notes

### OWASP LLM Top 10 Coverage

| Risk | F5 Mitigation |
|------|---------------|
| **LLM01 — Prompt Injection** | push_tick accepts only `user_id` + optional `now`; no free-text input that could be injected. Webhook URL from env only, not from model arguments. |
| **LLM06 — Sensitive Information Disclosure** | PHI allowlist filter on webhook payload strips all identifying/raw data. Audit logs contain no PHI, no raw payload, no full URL. |
| **LLM07 — Insecure Plugin Design** | push_tick has minimal surface (2 params). delivery.send webhook URL cannot be overridden by model. All inputs validated strictly. |
| **LLM08 — Excessive Agency** | Model cannot control scheduling policy (decide_due_tiers logic), webhook endpoint URL, or PHI filtering. The model triggers; the system decides. |

### PHI Protection (Constitution Principle VII)

1. **Webhook payload allowlist**: Only these fields pass through:
   - `delivery_id`, `push_id`, `tier`, `period_key`
   - `metrics.tir_pct`, `metrics.mean_mgdl`, `metrics.gmi`
   - `event_summaries[].type`, `event_summaries[].count`
   - `delivered_at`
2. **Deny-by-default**: Any field not in the allowlist is stripped.
3. **Endpoint from env only**: `CGM_WEBHOOK_URL` read at handler invocation time
   from environment. Model cannot supply URL through arguments.
4. **Audit log redaction**: Logs `delivery_url_domain` (parsed from URL), not
   the full URL. Logs `delivery_status` and HTTP status code, not response body.
5. **No credential leakage**: No auth headers sent in v1. If webhook auth is
   added later (HMAC signing), the secret key stays in env, never in logs.

## Complexity Tracking

No constitution violations — section intentionally empty.

## Notes

- **Test baseline (pre-F5)**: 374 tests green. F5's green standard = 374 do not
  regress, F5's new tests pass, and no NEW failures appear.
- **PushSchedulerService is already complete**: `services/scheduling/scheduler.py`
  implements `push_tick()`, `decide_due_tiers()`, `apply_silent_consent()`,
  `_emit()`, `_record_push()`. F5 does not modify this module.
- **HTTP client**: stdlib `urllib.request` for the POST. No external dependency.
  Adequate for a single POST with timeout. Can be swapped for `httpx` later if
  async or advanced features are needed.
- **Webhook signing**: Not in F5 scope. HMAC signing can be added as a
  non-breaking enhancement in a future feature (add `CGM_WEBHOOK_SECRET` env).
- **Analyze remediation (2026-06-09)**: a code-grounded `/speckit-analyze` pass
  confirmed all F5 code assumptions are accurate (`PushSchedulerService` + methods,
  `PushTickResult`, delivery `queued` branch, push-tick CLI-only, dotted tool-name
  convention, `cgm_` + name.replace('.','_') plugin derivation). Fixes applied:
  (S1) webhook MUST require `https://` and MUST NOT follow redirects; (D1) clarified
  v1 webhook payload is metadata-first, metrics deferred; (N1) tool renamed to the
  dotted `scheduling.push_tick` (external `cgm_scheduling_push_tick`) to match every
  other tool; plus a cron-registration doc task (FR-004) and an empty-window test.
  See spec Clarifications 2026-06-09.
