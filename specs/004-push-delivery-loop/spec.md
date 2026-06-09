# Feature Specification: Push Delivery Loop (F5)

**Feature Branch**: `004-push-delivery-loop`

**Created**: 2026-06-09

**Status**: Draft

**Input**: User description: "F5 主动推送 + 投递闭环（blast radius 最大）：合并条目 D1+D2。D1 push-tick 工具化 + cron：调度策略/静默即认可核心已完成（scheduling/scheduler.py），但 push-tick 仅 CLI、未在 registry/plugin.yaml。包成 Hermes tool 并接 Hermes cron。D2 delivery webhook/email 实现：当前仅 local_file 完整，email/webhook 记为 queued；先做 webhook HTTP POST。"

## Overview

This feature closes the push-delivery loop that turns the CGM agent from a
reactive system (user asks, agent answers) into a proactive companion (agent
pushes timely digests and delivers them through external channels). Two gaps
must be closed:

1. **D1 — push-tick toolization**: The tiered-push scheduling core
   (`PushSchedulerService`) already works end-to-end — it decides which tiers
   (daily/weekly/monthly) are due, emits content via `ConsolidationService`,
   records pushes idempotently, and advances silent-consent hypotheses. But it
   is only callable from internal code. The model and Hermes cron have no way to
   invoke it. This gap wraps `push_tick` as a registered, schema-validated Hermes
   tool and connects it to the Hermes cron system so the scheduling cadence is
   externally driven (per the Hermes Boundary principle).

2. **D2 — webhook delivery**: The `delivery.send` tool exists and handles
   `local_file` end-to-end, but `webhook` (and `email`) channels are recorded as
   `queued` with no actual HTTP POST. This gap implements `webhook` delivery as an
   HTTP POST to a user-configured endpoint, with strict PHI-redaction rules to
   satisfy the data-privacy constitution principle.

F5 has the **largest blast radius** of any pending feature: it touches shared
files (`registry.py`, `executor.py` `_DISPATCH`, `handlers/__init__.py`,
`plugin.yaml`) that F3/F4 also modify. The plan must address sequencing to avoid
merge conflicts.

## Clarifications

### Session 2026-06-09

- Q: push-tick tool 的输入参数应该包含什么？ → A: 仅 `user_id`（必需）和可选 `now`（ISO-8601 覆盖当前时间，方便测试）。调度策略、静默即认可、内容生成均由内部 `PushSchedulerService` 完成，模型不控制这些。
- Q: webhook 出网请求的超时和重试策略？ → A: 单次 HTTP POST，超时 10 秒，不重试（at-most-once）。失败记录到审计日志，delivery_status 设为 `failed`。重试属于 Hermes/cron 层职责，不在本层。
- Q: webhook payload 中允许包含哪些数据？是否允许携带聚合指标（TIR/mean）？ → A: 允许携带非识别性聚合指标（TIR%、mean mg/dL、GMI）和事件摘要，但严禁携带：用户姓名、Dexcom 凭证、原始血糖时间序列、任何可追溯到具体个人的数据点。payload 结构在发送前由硬编码的 allowlist 字段过滤。
- Q: 共享文件（registry.py, plugin.yaml, executor.py _DISPATCH, handlers/__init__.py）的更新如何避免与 F3/F4 冲突？ → A: F5 的共享文件修改仅限于追加操作（registry.py 新增一个 ToolSpec register、executor.py _DISPATCH 新增一个 key-value、plugin.yaml 新增一行、handlers/__init__.py 新增一个 import）。这些是纯追加，与 F3/F4 的追加操作不冲突。guard tests（ExecutorDispatchCoverageTests, plugin.yaml drift guard）在每个 feature 合入后自动覆盖新工具。
- Q: webhook endpoint URL 从哪里配置？ → A: 从环境变量 `CGM_WEBHOOK_URL` 读取（与现有 `CGM_AGENT_DB_PATH` 模式一致）。未设置时，webhook channel 返回 `failed` + 明确错误信息。不从 tool arguments 传入 URL（防止模型被注入恶意 endpoint）。

### Session 2026-06-09 (review remediation — autonomous, review-time confirmable)

Resolves findings from a code-grounded `/speckit-analyze` pass.

- Q: webhook 出网是否强制 https、是否跟随重定向？ → A: **强制 `https://`**（拒绝 http/其他 scheme，聚合健康指标不走明文）；**禁止跟随重定向**（30x 不得把 payload 转发到其他主机）。两者均加测试。见 tasks T017/T017b。
- Q: webhook payload 里的 `metrics`/`event_summaries` 内容从哪来？（`delivery.send` 当前只有 `user_id/channel/payload_ref`，`local_file` 仅写元数据） → A: v1 的 PHI allowlist **过滤器**是安全边界，对任意 manifest 生效；v1 实际 payload 以**元数据**为主（`delivery_id`/`push_id`/`tier`/`period_key`/`delivered_at`），聚合指标为 allowlist 允许项但仅在 manifest 含有时携带（push 触发且已解析 summary 时）。`payload_ref → summary → metrics` 的解析路径留作后续，不阻塞 v1。
- Q: push_tick 工具名是否遵循点分约定？ → A: 改为 `scheduling.push_tick`（外部名 `cgm_scheduling_push_tick`），与 `delivery.send`/`data.dexcom_sync` 等所有既有工具的 `group.action` 约定一致。见 tasks T002/T007/T008/T009。

## User Scenarios & Testing *(mandatory)*

### User Story 1 — push-tick 可被 Hermes cron 调度 (Priority: P1)

A Hermes cron job fires on schedule (e.g. daily 09:00 Asia/Shanghai) and calls
the `push_tick` tool with the user's ID. The system evaluates which tiers are
due, generates content for each, records the push, applies silent-consent
advancement, and returns a structured result. The model never controls scheduling
policy — it only triggers the tick.

**Why this priority**: This is the D1 backbone. Without the tool registration,
Hermes cron has nothing to call; the proactive loop cannot start. The scheduling
core already works — this is pure wiring.

**Independent Test**: Call `push_tick` with a user_id → verify `PushTickResult`
contains pushed tiers (if due) or empty list (if already pushed this period),
and silent-consent advancements (if applicable). Call again for same period →
idempotent (no duplicate push).

**Acceptance Scenarios**:

1. **Given** a user with CGM data and no prior push for today, **When** `push_tick`
   is called after `daily_hour`, **Then** a daily push is emitted with content and
   recorded in `push_events`.
2. **Given** a user with a `candidate` hypothesis older than the silence window,
   **When** `push_tick` is called, **Then** the hypothesis advances to `observing`
   and the advancement is audited.
3. **Given** a push was already recorded for this (user, tier, period), **When**
   `push_tick` is called again, **Then** no duplicate push is emitted (idempotent).
4. **Given** the push_tick tool is registered, **When** the model inspects tools,
   **Then** the `push_tick` schema is self-contained and invocable.

---

### User Story 2 — webhook 投递闭环 (Priority: P2)

After a push-tick generates content (or any delivery request targets the
`webhook` channel), the system makes an HTTP POST to the configured webhook
endpoint with a redacted payload. The payload contains aggregated metrics and
event summaries but no raw glucose points, no user identity, and no credentials.

**Why this priority**: Completes the delivery loop for external channels.
`local_file` already works; `webhook` is the first remote channel and validates
the PHI-redaction pipeline before `email` is added later.

**Independent Test**: Call `delivery.send` with `channel=webhook` and a valid
`payload_ref` → verify HTTP POST is sent to the configured endpoint, response
contains `delivery_status=sent`, and the audit log records the delivery without
PHI. Call with no `CGM_WEBHOOK_URL` set → verify `delivery_status=failed` with
clear error.

**Acceptance Scenarios**:

1. **Given** `CGM_WEBHOOK_URL` is configured, **When** `delivery.send` is called
   with `channel=webhook`, **Then** an HTTP POST is made to that URL with a
   JSON body, and `delivery_status=sent` is returned.
2. **Given** the webhook payload is being constructed, **When** the system
   assembles the body, **Then** only allowed fields (aggregate metrics, event
   summaries, tier/period metadata) are included; raw glucose points, user
   identifiers, and credentials are excluded.
3. **Given** `CGM_WEBHOOK_URL` is NOT set, **When** `delivery.send` is called
   with `channel=webhook`, **Then** `delivery_status=failed` is returned with a
   message indicating the endpoint is not configured.
4. **Given** the HTTP POST times out (10 seconds), **When** the request fails,
   **Then** `delivery_status=failed` is returned and the failure is audited.
5. **Given** a successful webhook delivery, **When** the audit log is written,
   **Then** the audit payload contains `delivery_id`, `channel`, `delivery_status`,
   and `delivery_url_domain` (not the full URL, to avoid logging secrets) — no
   PHI, no raw payload body, no credentials.

---

### Edge Cases

- **No CGM data for user**: push_tick runs but `decide_due_tiers` returns a tier
  with no data → content generation still runs (ConsolidationService handles
  empty windows); push is recorded with whatever content is generated.
- **Webhook endpoint returns non-2xx**: treated as failure; `delivery_status=failed`;
  status code recorded in audit; no retry.
- **Webhook endpoint unreachable (DNS/connection error)**: caught as failure within
  the 10-second timeout; `delivery_status=failed`; error type recorded in audit.
- **Model tries to pass a URL in tool arguments**: the `push_tick` tool does not
  accept URLs; the `delivery.send` tool uses `CGM_WEBHOOK_URL` from environment,
  not from arguments — the model cannot redirect delivery to a malicious endpoint
  (LLM07/08 defense).
- **Concurrent push_tick calls for same user/period**: idempotent via
  `push_events` UNIQUE constraint; second call returns empty pushed list.
- **PHI in webhook payload**: a defense-in-depth allowlist filter runs before
  HTTP POST, stripping any field not in the approved set. Even if upstream code
  accidentally includes raw data, it is filtered out.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST register `push_tick` as an active tool in the
  tool registry with a self-contained JSON schema requiring only `user_id` and
  accepting optional `now`.
- **FR-002**: The `push_tick` tool MUST invoke `PushSchedulerService.push_tick()`
  and return the structured `PushTickResult` (pushed tiers + silent-consent
  advancements).
- **FR-003**: The `push_tick` tool MUST be wired in `ToolExecutor._DISPATCH` and
  exposed in `plugin.yaml` `provides_tools`.
- **FR-004**: The `push_tick` tool MUST be invocable from Hermes cron (external
  timing driver) — the capability layer owns policy/content/state only, not
  scheduling cadence.
- **FR-005**: The `delivery.send` tool MUST implement webhook delivery as an HTTP
  POST to the endpoint configured in `CGM_WEBHOOK_URL`. The endpoint MUST use the
  `https://` scheme (http/other schemes → `failed`, no request), and the client
  MUST NOT follow redirects (a 30x → `failed`, payload not re-sent elsewhere).
- **FR-006**: Webhook payload MUST be filtered through a hard-coded allowlist
  before sending: only aggregate metrics (TIR%, mean mg/dL, GMI), event type
  summaries, tier/period metadata, and push_id are **permitted** (allowlist =
  security boundary, applied to any manifest). In v1 the payload is primarily
  metadata (`delivery_id`/`push_id`/`tier`/`period_key`/`delivered_at`); aggregate
  metrics are carried only when the manifest already contains them (push-triggered
  with a resolved summary). The `payload_ref → summary → metrics` resolution is
  deferred (does not block v1).
- **FR-007**: Raw glucose data points, user identifiers (user_id, name, email),
  and any credentials/tokens MUST NOT appear in the webhook payload.
- **FR-008**: Webhook HTTP POST MUST use a 10-second timeout and MUST NOT retry
  on failure.
- **FR-009**: When `CGM_WEBHOOK_URL` is not set, webhook delivery MUST return
  `delivery_status=failed` with a clear error message, and MUST NOT make any
  HTTP request.
- **FR-010**: The audit log for webhook delivery MUST record `delivery_id`,
  `channel`, `delivery_status`, `delivery_url_domain` (not full URL), and HTTP
  status code (on success) or error type (on failure). No PHI or raw payload.
- **FR-011**: The webhook endpoint URL MUST be read from the environment variable
  `CGM_WEBHOOK_URL` only — the model MUST NOT be able to supply or redirect the
  endpoint URL through tool arguments.
- **FR-012**: Guard tests (ExecutorDispatchCoverageTests, plugin.yaml drift guard)
  MUST continue to pass after push_tick is added, verifying the new tool is
  properly wired.
- **FR-013**: All changes MUST preserve constitution invariants: clinical numbers
  from deterministic code only (Principle I), track isolation (Principle II),
  safety routing (Principle III), persona tone (Principle IV), test-first
  (Principle V), traceable decisions (Principle VI), PHI privacy (Principle VII).
- **FR-014**: The existing test suite (baseline: 374 tests) MUST remain green,
  with new guard coverage for push_tick registration, webhook delivery, and
  PHI-redaction.

### Key Entities

- **PushTickResult**: the structured output of a push_tick invocation — contains
  `user_id`, `now` (ISO-8601), `pushed` (list of tier/period_key/push_id/content
  dicts), and `silent_consent` (list of hypothesis_id/statement/to dicts).
- **PushEvent**: a record in `push_events` table — `push_id`, `user_id`, `tier`,
  `period_key`, `summary_id`, `delivery_id`, `pushed_at`. UNIQUE on
  (user_id, tier, period_key) for idempotency.
- **DeliveryManifest**: the webhook request body — filtered to allowed fields only.
  Contains `delivery_id`, `push_id` (if push-triggered), `tier`, `period_key`,
  `metrics` (aggregate subset), `event_summaries`, `delivered_at`.
- **WebhookConfig**: environment-derived configuration — `CGM_WEBHOOK_URL`
  (endpoint), timeout (10s, hard-coded).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `push_tick` is registered as an active, dispatch-wired,
  plugin-declared tool; the guard tests (ExecutorDispatchCoverageTests +
  plugin.yaml drift) pass with the new tool included.
- **SC-002**: A Hermes cron job can invoke `push_tick` for a user and receive a
  valid `PushTickResult`; the push is recorded idempotently (second call for same
  period returns empty pushed list).
- **SC-003**: Calling `delivery.send` with `channel=webhook` results in an HTTP
  POST to the configured endpoint within 10 seconds; `delivery_status=sent` on
  2xx response.
- **SC-004**: The webhook payload contains zero instances of raw glucose data
  points, user identifiers, or credentials — verified by automated test asserting
  no `user_id`, no `points` array, no token fields in the filtered payload.
- **SC-005**: When `CGM_WEBHOOK_URL` is unset, webhook delivery returns
  `delivery_status=failed` with no HTTP request made; when the endpoint returns
  non-2xx or times out, `delivery_status=failed` is returned and audited.
- **SC-006**: The full automated test suite remains green (no regressions), with
  new guard coverage for push_tick registration/dispatch, webhook delivery,
  PHI-redaction allowlist, and env-var endpoint sourcing.

## Assumptions

- **Hermes cron is the external timing driver**: the capability layer owns
  push policy (which tier is due) and content; Hermes cron owns the schedule
  (when to call push_tick). This matches the Hermes Boundary principle.
- **Single local user / personal deployment**: push_tick takes a single
  `user_id`; multi-user fan-out is a Hermes/cron concern, not this layer's.
- **Webhook is the first remote delivery channel**: email delivery is deferred
  to a later feature. The webhook implementation validates the PHI-redaction
  pipeline that email will reuse.
- **`PushSchedulerService` is complete and tested**: the scheduling core
  (decide_due_tiers, push_tick, apply_silent_consent, _emit, _record_push) is
  already implemented in `services/scheduling/scheduler.py`. F5 wraps it as a
  tool, not re-implements it.
- **HTTP client**: stdlib `urllib.request` is sufficient for the simple POST;
  no external HTTP library (requests, httpx) is needed.
- **Webhook payload format**: JSON POST with `Content-Type: application/json`.
  No signing/HMAC for v1 (can be added later as a non-breaking enhancement).
- **Out of scope**: email delivery channel, webhook signature verification,
  delivery retry logic, multi-endpoint fan-out, delivery status tracking
  (read-back from remote).
