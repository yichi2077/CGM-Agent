# Phase 1 Data Model: Push Delivery Loop (F5)

F5 introduces **one new tool output entity** (PushTickResult as a tool response)
and **one new transient entity** (DeliveryManifest for webhook payload). It also
modifies the webhook branch of the existing `delivery.send` handler. No new
database tables or schema changes are required — `push_events` already exists.

## PushTickResult (tool output — not persisted by F5)

Already defined in `scheduler.py` as `PushTickResult`. F5 exposes it as the
tool's JSON response. The tool output schema matches `PushTickResult.to_dict()`.

| Field | Type | Description |
|-------|------|-------------|
| `status` | string ("ok") | Top-level envelope (from `_response_schema`) |
| `user_id` | string | The user whose push was evaluated |
| `now` | string (ISO-8601) | Timestamp of the tick |
| `pushed` | array of PushRecord | Tiers that were pushed this tick |
| `silent_consent` | array of ConsentRecord | Hypotheses advanced by silent consent |
| `evidence_refs` | array | Empty (push_tick does not produce evidence refs) |
| `audit_id` | string \| null | Audit log entry ID |

### PushRecord (element of `pushed`)

| Field | Type | Description |
|-------|------|-------------|
| `tier` | string ("daily"/"weekly"/"monthly") | Which tier was pushed |
| `period_key` | string | Period identifier (e.g., "2026-06-09", "2026-W24") |
| `push_id` | string (uuid) | Unique push record ID |
| `summary_id` | string | Reference to the generated summary |
| `content` | string | Generated push content (from ConsolidationService) |

### ConsentRecord (element of `silent_consent`)

| Field | Type | Description |
|-------|------|-------------|
| `hypothesis_id` | string | The hypothesis that was advanced |
| `statement` | string | Hypothesis statement text |
| `to` | string ("observing") | Target state (always "observing") |

## PushEvent (existing table — unchanged)

Already in the database schema. F5 reads/writes through `PushSchedulerService`
which is unchanged.

| Column | Type | Constraint |
|--------|------|------------|
| `push_id` | TEXT | PRIMARY KEY |
| `user_id` | TEXT | NOT NULL |
| `tier` | TEXT | NOT NULL |
| `period_key` | TEXT | NOT NULL |
| `summary_id` | TEXT | |
| `delivery_id` | TEXT | nullable (linked after delivery) |
| `pushed_at` | TEXT | ISO-8601 |

UNIQUE constraint: `(user_id, tier, period_key)` — idempotency backstop.

## DeliveryManifest (webhook request body — transient)

Constructed at webhook-send time from the push result or delivery request.
Filtered through the PHI allowlist before serialization. Never persisted as-is
(the delivery record lives in the `deliveries/` directory for `local_file`, and
in audit logs for all channels).

| Field | Type | Allowed | PHI Risk |
|-------|------|---------|----------|
| `delivery_id` | string (uuid) | ✅ | None |
| `push_id` | string \| null | ✅ | None |
| `tier` | string | ✅ | None |
| `period_key` | string | ✅ | None |
| `metrics` | object | ✅ (subset only) | Low — aggregate only |
| `metrics.tir_pct` | number | ✅ | Low — aggregate |
| `metrics.mean_mgdl` | number | ✅ | Low — aggregate |
| `metrics.gmi` | number | ✅ | Low — aggregate |
| `event_summaries` | array | ✅ (subset only) | Low — counts only |
| `event_summaries[].type` | string | ✅ | None |
| `event_summaries[].count` | integer | ✅ | None |
| `delivered_at` | string (ISO-8601) | ✅ | None |
| `user_id` | string | ❌ STRIPPED | High — PII |
| `content` | string | ❌ STRIPPED | Medium — personalized narrative |
| `points` | array | ❌ STRIPPED | High — raw glucose data |
| `summary_id` | string | ❌ STRIPPED | Low — internal ref |
| Any token/credential | — | ❌ STRIPPED | Critical |

## WebhookConfig (env-derived — transient)

Not persisted. Derived from environment at handler invocation time.

| Field | Source | Default | Notes |
|-------|--------|---------|-------|
| `url` | `CGM_WEBHOOK_URL` env var | (none) | Required for webhook delivery |
| `timeout_seconds` | Hard-coded | 10 | Not configurable |
| `max_retries` | Hard-coded | 0 | At-most-once delivery |

## State / lifecycle notes

- No schema migrations. `push_events` table already exists.
- The `delivery.send` tool's `channel` enum already includes "webhook" — F5
  activates the branch, not add it.
- Track isolation (Principle II) is unchanged — push scheduling reads from both
  CGM data and memory but does not merge tracks.
