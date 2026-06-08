# Phase 0 Research: Hermes Runtime Usability (F1)

All decisions are grounded in the current code (config.py, registry.py,
executor.py, storage/sqlite.py, integrations/hermes/*). Format: Decision /
Rationale / Alternatives considered.

## R1 — Canonical database path & key derivation

**Decision**: Route `AppConfig.from_env()` through the existing
`resolve_database_path()` (precedence: `CGM_AGENT_DB_PATH` → `<hermes_home>/cgm-agent/app.db`
→ `.runtime/app.db` dev fallback). Derive `storage_key_path` from the resolved DB
directory (`db.parent / "storage.key"`) instead of the standalone
`DEFAULT_STORAGE_KEY_PATH`. Emit a warning when an explicit
`CGM_AGENT_STORAGE_KEY_PATH` points outside the DB directory.

**Rationale**: `resolve_database_path()` is already correct and already used by
both plugins (`cgm/__init__.py:103`, `cgm_memory`). The only divergence is the CLI
entry via `from_env()` (config.py:92) plus the latent `storage_key_path` default
(config.py:75/93). `SQLiteStore.__init__` already defaults the key to
`db_path.parent/storage.key` (sqlite.py:66-68), so co-locating the AppConfig field
removes the last split point. Matches Clarifications 2026-06-08.

**Alternatives considered**: (a) Change `cgm` plugins to read `.runtime/` — rejected,
inverts the intended single source and breaks profile scoping. (b) No default,
require `CGM_AGENT_DB_PATH` — rejected, worsens first-run UX (Clarification chose
Hermes-home default).

## R2 — Flatten `events.create` schema

**Decision**: Replace the input `event` `{"$ref": "#/$defs/UserEvent"}` with an
inline object schema requiring only `event_type` + `ts_start`, with optional
`ts_end`, `payload`, `confidence`; `additionalProperties: false`. Also resolve or
remove the dangling output `$ref` for `timeseries.get_points` /
`timeseries.get_aggregate` (lower priority — output side).

**Rationale**: `_object_schema()` never attaches a `$defs` block (registry.py:108-118),
so every `#/$defs/...` is unresolved. For `events.create` this is on the **input**
side the model must fill — the direct cause of failed calls. Inlining is the
minimal, self-contained fix and keeps strict `additionalProperties:false`.

**Alternatives considered**: (a) Add a real `$defs` block generated from Pydantic
`model_json_schema()` — heavier, must verify Hermes resolves `$ref`; deferred. (b)
Leave output refs — acceptable short-term but cheap to fix alongside; include.

## R3 — Forced technical/provenance fields

**Decision**: In `_create_event`, before `UserEvent.model_validate(...)`, force on
the raw `event` dict: `event_id = uuid4`, `user_id = <outer user_id>`,
`created_by = "agent"`, `user_confirmed = false` — **hard overwrite, not
setdefault**. Keep the existing post-validate invariant checks. Optionally give
`UserEvent` defaults in domain/cgm.py for direct API ergonomics.

**Rationale**: Today `_create_event` (executor.py:258) requires the model to supply
these via `UserEvent`, which it cannot reliably do; and a model could otherwise set
`created_by:"user"`/`user_confirmed:true` to bypass the candidate gate (Damocles
W2). Overwrite-before-validate makes provenance unspoofable and satisfies the
existing `agent + user_confirmed` guard (executor.py:261).

**Alternatives considered**: `setdefault` — rejected (model can pre-set fields and
bypass). Validate-then-correct — rejected (provenance check would fire before
correction).

## R4 — `memory.confirm` / `memory.correct` reachability

**Decision**: Two-step. (1) **Diagnose** whether Hermes surfaces the `cgm_memory`
provider's `get_tool_schemas()` (returns `MEMORY_TOOL_SCHEMAS`, provider.py) to the
model — via an integration check asserting the tool names appear in the agent's
tool list. (2) **If not reachable**, register `memory.confirm/correct` on the `cgm`
standalone plugin (remove the `cgm/__init__.py:18` exclusion) AND make the provider
`get_tool_schemas()` return `[]` for those two, so exactly one invocable copy
exists (Damocles W3). If reachable, change nothing.

**Rationale**: The capability already exists behind the provider channel; whether
the model can call it is a Hermes runtime question, not a code defect. Diagnose
before changing architecture to avoid double-registration.

**Alternatives considered**: Unconditionally register on `cgm` — rejected (risks
duplicate tools / model confusion). Leave as-is — rejected (memory loop may be
unreachable, US3 fails).

## R5 — Non-destructive legacy migration

**Decision**: New `scripts/migrate_legacy_data.py` (and a `migrate-db` CLI entry):
copy **both** `.runtime/app.db` and `.runtime/storage.key` to the canonical dir;
refuse to overwrite an existing target without `--force`; refuse (clear message) if
the legacy key is missing; back up before overwrite when `--force`. CLI startup
detects a present legacy store and prints a migration hint. User-initiated only (no
auto-migrate), per Clarifications.

**Rationale**: The user's own `.runtime/app.db` exists today; moving the DB without
its Fernet key yields undecryptable data (Damocles W1). Co-moving DB+key and
refusing risky overwrites preserves the zero-data-loss success criterion (SC-004).

**Alternatives considered**: Auto-migrate on first run — rejected by Clarification
(prefer explicit). Dual-key re-encrypt merge — rejected as over-engineering for a
single-user personal store; conflict path just refuses + warns.

## R6 — First-run experience

**Decision**: `hermes-install` gains opt-in `--seed-demo`; an empty store yields a
gentle, persona-aligned prompt (via the memory provider `prefetch`/system-prompt
surface) explaining how to import/seed. No automatic seeding into a user store.

**Rationale**: Avoids a blank first-run (SC-005) without mixing demo data into real
user data (Clarification). Persona tone keeps it consistent with SOUL (Principle IV).

**Alternatives considered**: Auto-seed demo — rejected (pollutes real store). Silent
empty — rejected (poor first-run, the original complaint).
