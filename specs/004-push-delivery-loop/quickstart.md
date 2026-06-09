# Quickstart / Validation: Push Delivery Loop (F5)

Runnable validation tying each scenario to the spec's Success Criteria. Run on
the Hermes runtime venv. Use a throwaway `HERMES_HOME`/DB where noted.

Prereqs: project installed into the Hermes venv; `PYTHONPATH=src`; Hermes Agent
available on PATH.

## V1 — push_tick is registered and dispatch-wired (SC-001)

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_tool_registry tests.test_hermes_plugin_integration -v
```

**Expect**: `ExecutorDispatchCoverageTests` passes (push_tick in active set and
in _DISPATCH). `test_plugin_yaml_provides_tools_matches_runtime_registration`
passes (push_tick in plugin.yaml and runtime registration). No regressions.

## V2 — push_tick returns valid PushTickResult (SC-002)

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_push_tick_tool -v
```

**Expect**: Calling push_tick with a user_id returns status "ok" with `pushed`
and `silent_consent` lists. Second call for same period → `pushed` is empty
(idempotent). Schema has no unresolved `$ref`.

## V3 — Webhook delivery sends HTTP POST (SC-003)

```bash
CGM_WEBHOOK_URL=https://httpbin.org/post \
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_webhook_delivery -v
```

**Expect**: `delivery.send` with `channel=webhook` makes HTTP POST;
`delivery_status="sent"` on 2xx. Audit log records `delivery_url_domain`
("httpbin.org") and `http_status_code` (200).

## V4 — Webhook payload has no PHI (SC-004)

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_webhook_delivery.PHIFilterTests -v
```

**Expect**: Filtered webhook payload contains only allowed keys (delivery_id,
push_id, tier, period_key, metrics subset, event_summaries subset,
delivered_at). Injected `user_id`, `content`, `points` are stripped.

## V5 — Webhook failure modes (SC-005)

```bash
# No CGM_WEBHOOK_URL set:
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_webhook_delivery.WebhookFailureTests -v
```

**Expect**: Missing env → `delivery_status="failed"`, no HTTP request made.
Non-2xx response → `delivery_status="failed"`, error type in audit.
Timeout → `delivery_status="failed"`.

## V6 — No regressions (SC-006)

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests
```

**Expect**: Full suite green (≥ 374 baseline). New tests for push_tick
registration, webhook delivery, PHI filter, and idempotency all pass.

## V7 — Blast radius guard (shared files)

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest \
  tests.test_tool_registry.ExecutorDispatchCoverageTests \
  tests.test_hermes_plugin_integration.HermesPluginIntegrationTests \
  tests.test_hermes_plugin_integration.test_plugin_yaml_provides_tools_matches_runtime_registration -v
```

**Expect**: All guard tests pass. push_tick is in the active registry, in
_DISPATCH, in plugin.yaml, and in the runtime registration — all four agree.

## Done = all of V1–V7 pass and Constitution Check (plan.md) still holds post-implementation.
