# Phase 1 Data Model: Hermes Runtime Usability (F1)

F1 introduces **no new persistent entities**. It corrects how existing entities are
located, exposed, and written. Entities below are the ones this feature touches.

## CGM data store (location contract, not a new table)

- The single canonical SQLite store for a user; holds glucose points, events,
  memory layers (L1/L2/L3, candidates, summaries), reports, audit.
- **Resolution precedence** (single source of truth, used by CLI and both plugins):
  1. `CGM_AGENT_DB_PATH` (explicit override)
  2. `<hermes_home>/cgm-agent/app.db` (canonical default)
  3. `<project>/.runtime/app.db` (dev-only fallback)
- Invariant: file mode `0600`; created with parents as needed.

## Storage key

- Fernet key protecting PHI columns.
- **Location**: co-located with its store — `db_path.parent / "storage.key"` unless
  `CGM_AGENT_STORAGE_KEY` (inline) or `CGM_AGENT_STORAGE_KEY_PATH` (explicit path).
- Invariant: file mode `0600`; a store is decryptable iff its key is present in the
  same directory (or via env). Decryption failure surfaces an explicit error, never
  a silent `None`.

## UserEvent (write path corrected)

Existing model (domain/cgm.py). F1 changes only how it is **populated** for
agent-created events.

| Field | Type | Required from model? (after F1) | Source |
|---|---|---|---|
| `event_id` | string | **No** | system: `uuid4` (forced) |
| `user_id` | string | No | system: copied from outer `user_id` (forced) |
| `event_type` | enum (meal/exercise/medication/symptom/note/feedback/clinic_followup) | **Yes** | model |
| `ts_start` | datetime (ISO-8601) | **Yes** | model |
| `ts_end` | datetime \| null | No | model (optional) |
| `payload` | object | No | model (optional, e.g. meal details) |
| `confidence` | number [0,1] \| null | No | model (optional) |
| `created_by` | enum (user/agent/device) | **No** | system: `agent` (forced) |
| `user_confirmed` | bool | **No** | system: `false` (forced) |

**Agent-facing input shape** (flattened, inline — replaces the dangling `$ref`):

```jsonc
{
  "user_id": "string",                  // required
  "event": {                            // required
    "event_type": "meal|exercise|medication|symptom|note|feedback|clinic_followup", // required
    "ts_start": "2026-06-08T12:00:00Z", // required (ISO-8601)
    "ts_end":  "string|null",           // optional
    "payload": { },                     // optional
    "confidence": 0.0                    // optional [0,1]
  },
  "reason": "string|null"               // optional
}
```

Invariant: `event_id`, `created_by`, `user_confirmed` supplied by the model are
**overwritten** server-side; `additionalProperties:false` on `event`.

## MemoryCandidate (reachability only)

Existing entity. F1 does not change its shape; it ensures `memory.confirm` (promote
pending → durable L1) and `memory.correct` are invocable from a Hermes conversation
and registered exactly once. State transition unchanged: `pending → accepted → L1`.

## State / lifecycle notes

- No schema migrations. "Migration" here = relocating the DB file + key, not
  altering tables.
- Track isolation (`user_memory` vs `authoritative_kb`) is unchanged and must
  remain enforced after unifying the physical file (Principle II).
