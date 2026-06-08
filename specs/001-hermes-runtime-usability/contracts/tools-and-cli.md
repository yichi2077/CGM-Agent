# Phase 1 Contracts: Hermes Runtime Usability (F1)

Behavioral contracts the implementation must satisfy. These drive the tasks and the
regression tests.

## C1 — Resolved database path (config contract)

- `AppConfig.from_env().database_path` MUST equal `resolve_database_path(HERMES_HOME)`.
- Precedence: `CGM_AGENT_DB_PATH` > `<hermes_home>/cgm-agent/app.db` > `.runtime/app.db`.
- `AppConfig.from_env()` and both Hermes plugins MUST resolve the **same** path for
  the same environment.
- `storage_key_path` MUST default to `database_path.parent / "storage.key"`.
- If `CGM_AGENT_STORAGE_KEY_PATH` resolves to a different directory than the DB, a
  warning MUST be logged.

**Tests**: `tests/test_config.py` — override precedence; Hermes-home default;
key co-location; plugin/CLI agreement.

## C2 — `events.create` tool contract (flattened)

Request (model-facing):

```jsonc
{ "user_id": "demo-user",
  "event": { "event_type": "meal", "ts_start": "2026-06-08T12:00:00Z",
             "payload": { "description": "午饭" } } }
```

- Required: `user_id`, `event.event_type`, `event.ts_start`. Everything else optional.
- The schema MUST be self-contained (no `$ref`/`$defs`); `event` is
  `additionalProperties:false`.
- Server MUST force `event_id` (uuid), `user_id` (= outer), `created_by="agent"`,
  `user_confirmed=false`, overriding any model-supplied values.

Response (success): `status:"ok"`, `payload.event_id` set, `payload.event.created_by="agent"`,
`payload.event.user_confirmed=false`.

**Tests**: minimal-fields success; model passing `created_by:"user"` /
`user_confirmed:true` / a fake `event_id` → all overwritten; invalid arg → strict
rejection (no coercion).

## C3 — `migrate-db` CLI contract

```
python -m hermes_cgm_agent migrate-db [--dry-run] [--force]
```

- Copies BOTH `.runtime/app.db` and `.runtime/storage.key` to the canonical dir.
- No legacy store → prints "nothing to migrate", exit 0.
- Legacy DB present but legacy key missing → REFUSE with clear message (would yield
  undecryptable data), non-zero exit.
- Target already has data and no `--force` → REFUSE (warn), non-zero exit.
- `--force` → back up the target first, then overwrite.
- `--dry-run` → print planned copies, make no changes.
- MUST NOT print key bytes or any secret to stdout/logs.

**Tests**: `tests/test_migrate_legacy_data.py` — happy path; missing-key refusal;
existing-target refusal; dry-run no-op; backup-on-force.

## C4 — Memory tool reachability contract

- After F1, `memory.confirm` and `memory.correct` are invocable by the model in a
  Hermes conversation, and each appears **exactly once** in the tool list.
- If both the standalone `cgm` plugin and the `cgm_memory` provider could expose
  them, exactly one path is active (the other returns no schema for those two).

**Tests**: `tests/test_hermes_plugin_integration.py` — assert single registration;
no duplicate `cgm_memory_confirm` / `memory.confirm`.

## C5 — First-run / empty-store contract

- `hermes-install --seed-demo` (opt-in) seeds demo data; absent the flag, nothing is
  seeded.
- An empty store yields a single, gentle, persona-aligned prompt telling the user
  how to import/seed; never a bare "no data" / blank.

**Tests**: empty-store prompt emitted once; `--seed-demo` flag seeds; default install
does not seed.

## Cross-cutting (constitution)

- No clinical numbers produced by the model (Principle I).
- Track isolation preserved post-unification (Principle II).
- DB + key `0600`; no secrets in audit/logs (Principle VII).
- Full test suite green; new guards added (Principle V).
