# Phase 0 Research: Push Delivery Loop (F5)

All decisions are grounded in the current code (scheduler.py, registry.py,
executor.py, handlers/delivery.py, plugin.yaml). Format: Decision /
Rationale / Alternatives considered.

## R1 — push_tick tool interface design

**Decision**: Register the tool with the dotted registry name **`scheduling.push_tick`**
(external `cgm_scheduling_push_tick`), matching the `group.action` convention of every
other tool (analyze N1). Input schema: `user_id` (required string) + `now` (optional
ISO-8601 datetime for test override). Output schema wraps `PushTickResult.to_dict()`.
The tool invokes `PushSchedulerService.push_tick(user_id=user_id, now=now)` using the
same store/audit wiring as other handler mixins (handler method stays `_push_tick`).

**Rationale**: The scheduling core is already complete and tested. The tool
wrapper adds JSON-boundary validation (Principle V) and makes it invocable from
Hermes cron. Keeping the interface minimal (1 required + 1 optional param)
follows the principle that the model triggers but the system decides — the model
cannot control which tiers are due, what content is generated, or how
silent-consent works.

**Alternatives considered**: (a) Expose `decide_due_tiers` as a separate tool —
rejected, splits the decision/action atomically and leaks internal scheduling
semantics to the model. (b) Accept config overrides in tool arguments — rejected,
model should not control scheduling policy (LLM08). (c) Accept `timezone` param
— rejected, the config default (`Asia/Shanghai`) is the user's setting; per-user
timezone is a future concern.

## R2 — push_tick handler architecture

**Decision**: Create a new `PushTickHandlerMixin` in
`handlers/push_tick.py` that builds a `PushSchedulerService` lazily (from
`self.repository.store` + `self.audit_service`) and calls `push_tick()`. Wire
it into `ToolExecutor` via inheritance + `_DISPATCH` entry. This follows the
existing per-domain mixin pattern (TimeseriesHandlerMixin, EventHandlerMixin,
etc.).

**Rationale**: The handler mixin pattern is the established architecture (F1/G1).
Each domain has its own mixin module; push scheduling is a new domain. The
mixin reads shared executor state (`repository`, `audit_service`, `registry`)
and the shared error path through `BaseToolHandler`.

**Alternatives considered**: (a) Inline in executor.py — rejected, violates the
mixin separation pattern. (b) Call scheduler directly from executor — rejected,
bypasses the handler abstraction.

## R3 — Webhook delivery implementation

**Decision**: In `DeliveryHandlerMixin._delivery_send`, when `channel == "webhook"`:
(1) Read `CGM_WEBHOOK_URL` from `os.environ`. If unset, return error. (2) Build
a `DeliveryManifest` dict from the push result (or from `payload_ref` lookup)
filtered through a hard-coded allowlist. (3) HTTP POST via `urllib.request.urlopen`
with 10-second timeout. (4) On 2xx → `delivery_status=sent`; on non-2xx or
exception → `delivery_status=failed`. (5) Audit with domain-only URL, status
code, no PHI.

**Rationale**: The existing `_delivery_send` already handles `local_file` and
returns `queued` for `webhook`. This replaces the `queued` branch with actual
HTTP logic. stdlib `urllib.request` avoids adding a dependency. The allowlist
filter is a defense-in-depth layer (Principle VII).

**Alternatives considered**: (a) `requests`/`httpx` library — rejected, adds
external dependency for a single POST; stdlib is adequate. (b) Async HTTP —
rejected, the handler is synchronous (matching all other handlers); Hermes cron
is the async layer. (c) Retry with backoff — rejected, at-most-once delivery;
retries are a Hermes/cron concern. (d) Webhook URL from tool arguments —
rejected, model could be injected to send data to a malicious endpoint (LLM01/07).

## R4 — PHI allowlist for webhook payload

**Decision**: Hard-coded allowlist of permitted fields:
- `delivery_id` (string)
- `push_id` (string, if push-triggered)
- `tier` (string: daily/weekly/monthly)
- `period_key` (string)
- `metrics` (object, subset: `tir_pct`, `mean_mgdl`, `gmi` only)
- `event_summaries` (array of `{type, count}` only)
- `delivered_at` (ISO-8601)

Everything else is stripped before HTTP POST. The filter runs on the final dict
just before serialization.

**Rationale**: The webhook delivers aggregated insights, not raw health data.
The allowlist is intentionally narrow — it's easier to add fields later than to
remove them after a PHI leak. The metrics subset (`tir_pct`, `mean_mgdl`, `gmi`)
are aggregate statistics that cannot be reverse-engineered to individual glucose
readings.

**Alternatives considered**: (a) Blocklist (deny specific fields) — rejected,
allowlist is safer (deny-by-default). (b) Send full push result — rejected,
includes content text that could contain personalized narrative. (c) Encrypt
payload — rejected for v1, adds complexity; the allowlist approach avoids PHI
entirely rather than protecting it in transit.

## R5 — Endpoint configuration source

**Decision**: Read `CGM_WEBHOOK_URL` from `os.environ` at handler invocation
time (not at import time or init time). If unset, return `delivery_status=failed`
with message "webhook endpoint not configured (CGM_WEBHOOK_URL not set)".

**Rationale**: Consistent with existing env-var pattern (`CGM_AGENT_DB_PATH`).
Reading at invocation time allows the env to be set/changed without restarting
the process. Not reading from tool arguments prevents the model from redirecting
delivery to a malicious endpoint (LLM07 — insecure plugin design).

**Alternatives considered**: (a) Tool argument for URL — rejected (security, see
above). (b) Config file — rejected, adds a new config surface for one setting.
(c) Database-stored config — rejected, over-engineering for a single env var.

## R6 — Audit logging for external deliveries

**Decision**: For webhook deliveries, audit log records:
- `tool_name`: "delivery.send"
- `delivery_id`, `channel` ("webhook"), `delivery_status` ("sent"/"failed")
- `delivery_url_domain`: parsed from `CGM_WEBHOOK_URL` (e.g., "hooks.example.com")
- `http_status_code`: on success (e.g., 200)
- `error_type`: on failure (e.g., "timeout", "connection_refused", "http_4xx")
- No: full URL, response body, request body, PHI, credentials

**Rationale**: Principle VII requires no secrets/tokens in audit payloads.
Logging the domain (not full URL) aids debugging without exposing the full
endpoint path which could contain tokens in the URL. Logging the HTTP status
code aids troubleshooting.

**Alternatives considered**: (a) Log full URL — rejected, could contain secrets
in query params. (b) Log response body — rejected, could contain echoed PHI.
(c) No audit for external deliveries — rejected, violates traceability.
