# Feature Specification: Hermes Runtime Usability (F1)

**Feature Branch**: `001-hermes-runtime-usability`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "F1 — Hermes 运行可用性（MVP 阻断级）。统一 CLI 与 Hermes 插件的数据库路径 + Fernet key、展平悬空工具 schema 并强制技术字段、修复 memory.confirm/correct 可达性。合并 backlog 条目 A1/A2/A3/A5。"

## Overview

This feature removes the blockers that currently prevent the CGM agent from
being usable through the Hermes shell. Today a user can import data with the
local CLI but the agent in Hermes sees an empty, separate database; the agent
also frequently fails to record events, and the user cannot confirm what the
agent remembers. The goal is a single coherent runtime where **what the user
puts in is what the agent sees, the agent can reliably record what happened, and
the user stays in control of the agent's memory** — without weakening any of the
medical-safety, dual-track-isolation, or privacy guarantees in the project
constitution.

This is the dependency spine of the backlog (see [BACKLOG.md](../../docs/BACKLOG.md));
nothing else can be verified end-to-end in real Hermes until it lands.

## Clarifications

### Session 2026-06-08

- Q: 检测到旧 `.runtime/app.db` 时迁移如何触发？ → A: 检测 + 提示，用户手动运行迁移命令；系统不自动迁移。
- Q: 单一真实数据库路径以哪个为准？ → A: `~/.hermes/cgm-agent/app.db`（Hermes-home 派生）为单一真实源；`.runtime/` 仅开发回退；`CGM_AGENT_DB_PATH` 覆盖优先。
- Q: 新旧两个库都有数据时如何处理？ → A: 拒绝静默覆盖，警告并要求显式确认（`--force`）。
- Q: 空库首次体验如何做？ → A: `hermes-install` 提供可选 `--seed-demo` + 空库友好提示；不自动向用户库塞示例数据。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - I can see my own data in Hermes (Priority: P1)

A user imports their CGM data (or runs the demo seed) through the local CLI,
then opens a normal Hermes conversation and asks about their glucose. The agent
answers from that same data — not from an empty store.

**Why this priority**: This is the top blocker. Until the CLI and the Hermes
plugins read and write the same store, every other capability the agent exposes
returns nothing in a real conversation, and the product is unusable.

**Independent Test**: Import/seed data via CLI for a user, then in a Hermes
conversation query that user's recent points/aggregate; confirm the agent
returns the imported data for the same time window.

**Acceptance Scenarios**:

1. **Given** a user has imported CGM points via the CLI, **When** the user asks the agent about that window in a Hermes conversation, **Then** the agent reports metrics derived from those exact points (not "no data").
2. **Given** the user previously stored data under the legacy standalone location, **When** the runtime is upgraded, **Then** that prior data is preserved and remains readable (decryptable), or the user is clearly told how to migrate it — data is never silently lost or overwritten.
3. **Given** an operator has set an explicit database-location override, **When** the CLI and the agent both run, **Then** both honor that override and resolve to the same store.

---

### User Story 2 - The agent reliably records what happened (Priority: P2)

During a conversation the user mentions a meal/exercise/symptom. The agent
records it as an event by supplying only the essentials (what it was and when it
started); the system fills in the bookkeeping and marks it as an unconfirmed,
agent-created candidate.

**Why this priority**: Event capture is core to the companion loop, but it
depends on US1 being in place to be verifiable. Today the agent must guess
opaque bookkeeping fields and the event tool definitions are unresolvable, so the
agent's attempts fail.

**Independent Test**: Have the agent create an event providing only event type
and start time; confirm it succeeds, the event is stored as agent-created and
unconfirmed, and a unique identifier was assigned by the system.

**Acceptance Scenarios**:

1. **Given** the agent wants to log an event, **When** it provides only the event type and start time, **Then** the event is created successfully without the agent supplying any internal identifier or status field.
2. **Given** the agent (or model) tries to mark an event it creates as user-confirmed or as user-authored, **When** the event is recorded, **Then** the system forces it to agent-created and unconfirmed — the model cannot override these safety/provenance fields.
3. **Given** the agent inspects the available tools, **When** it reads an event/timeseries/aggregate tool definition, **Then** the parameter shape is fully self-contained (no unresolved references) so it can fill arguments without guessing.

---

### User Story 3 - I stay in control of what the agent remembers (Priority: P3)

The agent proposes a memory candidate (e.g. a possible pattern). In the same
Hermes conversation the user can confirm it (promoting it to durable memory) or
correct it. The memory loop closes inside the conversation.

**Why this priority**: Completes the consent-based memory loop. It depends on the
unified store (US1) to read/write the same candidates and is lower-frequency than
data visibility and event capture.

**Independent Test**: With a pending memory candidate present, invoke
confirm from a Hermes conversation; confirm the candidate is promoted to
long-term memory and is afterwards retrievable.

**Acceptance Scenarios**:

1. **Given** a pending memory candidate exists, **When** the user confirms it through the agent in a Hermes conversation, **Then** it is promoted to durable (L1) memory and becomes retrievable.
2. **Given** the user wants to correct a stored memory, **When** they ask the agent to correct it, **Then** the correction is applied and audited.
3. **Given** the memory confirm/correct capability is offered, **When** the agent lists tools, **Then** exactly one invocable version of each is visible — never duplicate or conflicting registrations.

---

### Edge Cases

- **Both stores hold data**: legacy and current locations both contain data → the system MUST warn and MUST NOT silently overwrite or merge; the user explicitly chooses.
- **Key missing on migration**: if the encryption key for legacy data is absent, migration MUST refuse rather than produce undecryptable data, and MUST say so clearly.
- **Explicit override present**: an explicit database-location override always wins over both default locations, for CLI and agent alike.
- **Empty store on first run**: a brand-new user with no data gets a clear, friendly prompt explaining how to seed/import — not a blank or confusing "no data" silence.
- **Provenance tampering attempt**: the model supplies a fabricated identifier or a "confirmed/user-authored" flag for an agent-created event → forced back to safe values.
- **Decryption failure**: an encrypted field that cannot be decrypted surfaces an explicit error, never a silent empty/`None`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve a single canonical data store that the CLI and the Hermes plugins both read from and write to for a given user, so imported data is visible in conversation.
- **FR-002**: The encryption key MUST always be co-located with the data store it protects, so a correctly located store is always decryptable.
- **FR-003**: An explicit operator-provided store-location override MUST take precedence for every entry point (CLI and agent), keeping them in agreement.
- **FR-004**: The system MUST provide a migration path for data held at the legacy standalone location that moves both the data and its encryption key together. Migration is **user-initiated**: the system MUST detect a present legacy store and prompt the user to run an explicit migration command, and MUST NOT auto-migrate.
- **FR-005**: Migration MUST be non-destructive: when both the legacy and target stores hold data it MUST warn and refuse to overwrite without an explicit `--force` confirmation, and MUST refuse (with a clear message) when it would otherwise yield undecryptable data.
- **FR-006**: The agent MUST be able to create an event by supplying only the event type and start time; all internal/bookkeeping fields MUST be supplied by the system.
- **FR-007**: For agent-created events, the system MUST force provenance to "agent-created" and status to "unconfirmed", overriding any value the model supplies; the model MUST NOT be able to set these.
- **FR-008**: All tool definitions exposed to the agent MUST be self-contained (no unresolved references), so the agent can construct valid arguments without guessing.
- **FR-009**: Invalid arguments crossing the tool boundary MUST be rejected with clear, strict validation (no silent type coercion), consistent with the existing tool-argument discipline.
- **FR-010**: The memory confirm and correct capabilities MUST be invocable from within a Hermes conversation, and each MUST be exposed exactly once (no duplicate/conflicting registrations).
- **FR-011**: Confirming a pending memory candidate MUST promote it to durable memory such that it is afterwards retrievable; correcting a memory MUST apply and audit the change.
- **FR-012**: A first-run user with an empty store MUST receive a clear prompt on how to seed or import data, and the installer MUST offer an opt-in `--seed-demo` to load demo data; demo data MUST NOT be seeded automatically into a user's store.
- **FR-013**: The change MUST preserve all constitution invariants: clinical numbers come only from deterministic analytics (never the model); the personal and authoritative memory tracks remain isolated; the data store and key remain access-restricted (owner-only); and no secrets/tokens appear in audit payloads or logs.
- **FR-014**: The existing automated test suite MUST remain green, and the unified-path, forced-provenance, and single-registration behaviors MUST each be covered by regression tests.

### Key Entities *(include if feature involves data)*

- **CGM data store**: the single canonical per-user store holding glucose points, events, memory, audit; the one source of truth both entry points use.
- **Storage key**: the secret protecting encrypted health fields; bound to its store's location.
- **User event**: a recorded meal/exercise/symptom/etc. with type, start time, system-assigned identifier, provenance (agent/user/device), and confirmation status.
- **Memory candidate**: a pending memory awaiting user confirmation before becoming durable memory.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a CLI import/seed for a user, 100% of the imported points and events for that window are visible when the agent is queried in a Hermes conversation.
- **SC-002**: The agent can create a valid event supplying only two fields (type + start time) with at least a 95% success rate across repeated trials, and every resulting event is recorded as agent-created and unconfirmed.
- **SC-003**: A pending memory candidate can be confirmed from within a Hermes conversation and is retrievable afterward, in at least 95% of attempts.
- **SC-004**: Zero data loss across migration: 100% of previously stored, correctly keyed data remains readable after upgrade/migration.
- **SC-005**: A first-run (empty store) user reaches their first visible data state within 5 minutes, guided only by the in-product prompt, with no network dependency required.
- **SC-006**: The full automated test suite remains green (no regressions), with new guard coverage for path unification, forced provenance, and single tool registration.

## Assumptions

- **Single local user / personal deployment**: this is a personal CGM agent; multi-tenant/multi-profile concerns are out of scope for this feature.
- **Canonical location**: the Hermes-home-derived store location is the single source of truth; the project-local standalone location is a development-only fallback; an explicit override env var still wins. (Decided — see Clarifications 2026-06-08.)
- **Hermes home resolves consistently** across the tool plugin and the memory provider so both entry points compute the same store path.
- **Known root causes (for planning, not this spec)**: the config loader currently bypasses the shared path resolver; some tool schemas carry unresolved `$ref` references with no definitions; the standalone plugin currently excludes the two memory-review tools. The HOW lives in `plan.md`.
- **Out of scope for F1**: offline/first-run model-download hardening for the demo seed (backlog A4) and the multi-day `occurred_at` non-collapse verification (backlog A6) are tracked separately and may be folded in only after diagnosis.
- **Data source**: real-data ingestion strategy (Dexcom is frozen; CSV/seed is the near-term input) is decided separately in the F2 data-source ADR; F1 assumes CSV/seed-demo as the input path.
