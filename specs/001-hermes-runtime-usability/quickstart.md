# Quickstart / Validation: Hermes Runtime Usability (F1)

Runnable validation tying each scenario to the spec's Success Criteria. Run on the
Hermes runtime venv. Use a throwaway `HERMES_HOME`/DB where noted to avoid touching
the real runtime store.

Prereqs: project installed into the Hermes venv; `PYTHONPATH=src`; Hermes Agent
available on PATH.

## V1 — Data visible in Hermes (SC-001, US1)

1. Seed a throwaway store:
   `... -m hermes_cgm_agent seed-demo --db-path ./.runtime/demo.db`
   (or, post-fix, seed into the canonical path).
2. Confirm CLI and plugin resolve the same path:
   `... -m hermes_cgm_agent dev-status` → note DB path.
3. In a Hermes conversation: ask for recent points/aggregate for the seeded user.
4. **Expect**: agent reports metrics derived from the seeded data — not "no data".

## V2 — Path unification (SC-001) — unit

`... -m unittest tests.test_config -v`
**Expect**: `AppConfig.from_env().database_path == resolve_database_path(HERMES_HOME)`;
override precedence holds; key co-located.

## V3 — Event creation with minimal fields (SC-002, US2)

Call `events.create` with only `user_id` + `event.event_type` + `event.ts_start`
(e.g. via the executor test harness or a Hermes tool call).
**Expect**: `status:"ok"`; `event_id` is a uuid; `created_by="agent"`;
`user_confirmed=false`. Repeating with model-supplied `created_by:"user"` /
`user_confirmed:true` / fake `event_id` → all overwritten.

## V4 — Memory loop closes (SC-003, US3)

1. Generate a report / produce a pending memory candidate.
2. In a Hermes conversation, confirm the candidate via the memory tool.
3. **Expect**: candidate promoted to L1 and retrievable; exactly one memory-confirm
   tool visible (no duplicate).

## V5 — Migration is safe (SC-004)

1. With a legacy `.runtime/app.db` + `.runtime/storage.key` present:
   `... -m hermes_cgm_agent migrate-db --dry-run` → lists DB+key copies, no changes.
2. `... migrate-db` → both copied to canonical dir; data decryptable afterward.
3. Negative: remove legacy key, retry → refuses with a clear message (no
   undecryptable data produced). Target with data + no `--force` → refuses.

## V6 — First run (SC-005)

1. Point at an empty canonical store.
2. **Expect**: a single gentle prompt on how to import/seed (persona tone).
3. `hermes-install --seed-demo` → demo data present; plain install → empty.

## V7 — No regressions (SC-006)

`... -m unittest discover -s tests`
**Expect**: full suite green (≥ prior baseline); new guards for path unification,
forced provenance, single registration, and migration all present and passing.

## Done = all of V1–V7 pass and Constitution Check (plan.md) still holds post-implementation.
