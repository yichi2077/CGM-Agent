# Phase 1 Contracts: Push Delivery Loop (F5)

Behavioral contracts the implementation must satisfy. These drive the tasks and
the regression tests.

## C1 — `push_tick` tool contract

### Input (model-facing)

```jsonc
{
  "user_id": "demo-user",        // required
  "now": "2026-06-09T09:00:00+08:00"  // optional, ISO-8601, for testing override
}
```

- Required: `user_id` (string).
- Optional: `now` (ISO-8601 datetime string). When omitted, uses current time.
- The schema MUST be self-contained (no `$ref`/`$defs`); `additionalProperties: false`.
- The model CANNOT control: scheduling policy, tier selection, content generation,
  silent-consent logic. These are internal to `PushSchedulerService`.

### Output (success)

```jsonc
{
  "status": "ok",
  "evidence_refs": [],
  "audit_id": "abc123",
  "user_id": "demo-user",
  "now": "2026-06-09T09:00:00+08:00",
  "pushed": [
    {
      "tier": "daily",
      "period_key": "2026-06-09",
      "push_id": "uuid-hex",
      "summary_id": "uuid-hex",
      "content": "..."
    }
  ],
  "silent_consent": [
    {
      "hypothesis_id": "uuid-hex",
      "statement": "...",
      "to": "observing"
    }
  ]
}
```

### Idempotency

Calling `push_tick` twice for the same `(user_id, tier, period_key)` MUST NOT
produce duplicate pushes. The second call returns `pushed: []` for that tier.

### Registration

- Tool name: `scheduling.push_tick` (dotted `group.action` convention; external `cgm_scheduling_push_tick` — analyze N1)
- Group: `scheduling`
- Status: `active`
- Risk level: `write` (modifies push_events + hypothesis states)
- Owner module: `push_scheduler`

**Tests**: `tests/test_push_tick_tool.py` — invoke with user_id, verify
PushTickResult shape; invoke twice for same period, verify idempotent; verify
schema has no unresolved refs; verify silent-consent advancement is audited.

## C2 — `delivery.send` webhook contract

### Existing tool (modified behavior)

The `delivery.send` tool already exists with `channel` enum
`["local_file", "email", "webhook"]`. F5 activates the `webhook` branch.

### Input (unchanged)

```jsonc
{
  "user_id": "demo-user",
  "channel": "webhook",
  "payload_ref": "push:uuid-hex"   // reference to push result or delivery payload
}
```

### Webhook delivery flow

1. Validate `user_id`, `channel`, `payload_ref` (existing validation).
2. Read `CGM_WEBHOOK_URL` from `os.environ`.
   - NOT SET → return `status:"ok"`, `delivery_status:"failed"`, message in
     audit. No HTTP request made.
3. Resolve `payload_ref` to a delivery manifest (filtered dict).
4. Apply PHI allowlist filter (C3 below).
5. HTTP POST to `CGM_WEBHOOK_URL`:
   - `Content-Type: application/json`
   - Body: filtered manifest as JSON
   - Timeout: 10 seconds
   - No retry
6. Response handling:
   - 2xx → `delivery_status:"sent"`
   - Non-2xx → `delivery_status:"failed"`
   - Exception (timeout, DNS, connection) → `delivery_status:"failed"`
7. Audit log (C4 below).

### Output

```jsonc
{
  "status": "ok",
  "evidence_refs": [],
  "audit_id": "abc123",
  "delivery_id": "uuid-hex",
  "delivery_status": "sent",     // or "failed"
  "manifest_path": null          // only set for local_file channel
}
```

**Tests**: `tests/test_webhook_delivery.py` — successful POST with 2xx;
non-2xx response → failed; timeout → failed; missing env → failed; no HTTP
request when env unset; verify no PHI in request body.

## C3 — PHI allowlist contract

Before HTTP POST, the delivery manifest dict is filtered:

### Allowed fields (pass-through)

| Path | Type | Description |
|------|------|-------------|
| `delivery_id` | string | Unique delivery ID |
| `push_id` | string \| null | Push record ID (if push-triggered) |
| `tier` | string | daily/weekly/monthly |
| `period_key` | string | Period identifier |
| `metrics.tir_pct` | number | Time in range percentage |
| `metrics.mean_mgdl` | number | Mean glucose |
| `metrics.gmi` | number | Glucose Management Indicator |
| `event_summaries[].type` | string | Event type (meal, exercise, etc.) |
| `event_summaries[].count` | integer | Count of events |
| `delivered_at` | string | ISO-8601 timestamp |

### Denied fields (stripped)

Everything else, including but not limited to:
`user_id`, `content`, `points`, `summary_id`, `session_id`, any token/credential,
any field containing raw glucose readings, any free-text narrative.

**Tests**: Assert the filtered output contains only allowed keys. Assert that
injecting `user_id`, `content`, `points`, `session_id` into the input results
in them being absent from the output. Assert nested objects are also filtered
(event_summaries only has type+count).

## C4 — Audit logging contract (webhook)

Audit payload for webhook deliveries:

```jsonc
{
  "tool_name": "delivery.send",
  "status": "ok",
  "data_scope": {"user_id": "demo-user"},
  "risk_level": "external",
  "evidence_refs": [],
  "delivery_id": "uuid-hex",
  "channel": "webhook",
  "delivery_status": "sent",
  "delivery_url_domain": "hooks.example.com",  // domain only, not full URL
  "http_status_code": 200,                      // on success
  "error_type": null                            // on failure: "timeout"/"http_error"/"connection_error"
}
```

- MUST NOT contain: full URL, request body, response body, PHI, credentials.
- `delivery_url_domain` is parsed from `CGM_WEBHOOK_URL` via `urllib.parse.urlparse`.

**Tests**: Verify audit payload keys and types. Verify `delivery_url_domain` is
the domain portion only. Verify no `user_id` value, no `points`, no raw content
in audit.

## C5 — Environment variable contract

- `CGM_WEBHOOK_URL`: webhook endpoint URL (e.g., `https://hooks.example.com/cgm`).
  Read at handler invocation time (not import/init time).
- If unset: webhook delivery returns `delivery_status:"failed"` with no HTTP call.
- If set to an invalid URL: treated as a configuration error; `delivery_status:"failed"`
  with `error_type:"invalid_url"`.

**Tests**: Patch `os.environ` with/without `CGM_WEBHOOK_URL`; verify behavior
in both cases. Verify URL is not read from tool arguments.

## Cross-cutting (constitution)

- No clinical numbers produced by the model (Principle I).
- Track isolation preserved (Principle II).
- DB + key `0600`; no secrets in audit/logs/webhook payload (Principle VII).
- Full test suite green; new guards added (Principle V).
- DECISION_LOG entry for new tool + webhook channel (Principle VI).
