# Phase 1 Data Model: Medical Safety Hardening (F3)

F3 enhances existing entities and adds two new lightweight ones. No schema
migrations; changes are to in-memory structures and tool contracts.

## ClaimCard (enhanced — existing entity)

Existing model in `services/rag/authoritative.py`. F3 adds enforcement but
no new fields.

| Field | Type | F3 Change | Notes |
|-------|------|-----------|-------|
| `card_id` | string | unchanged | Primary key |
| `title` | string | unchanged | |
| `claim_zh` | string | unchanged | Chinese claim text |
| `claim_en` | string | unchanged | English claim text |
| `verified` | bool | **enforced** | `true` now requires `reviewer`+`reviewed_at` (validator already checks) |
| `tier` | string | **enforced** | `kb.approve` rejects `tier=auto` cards |
| `reviewer` | string \| None | **enforced** | Must be non-empty when `verified=true` |
| `reviewed_at` | string \| None | **enforced** | Must be non-empty when `verified=true` |
| `source` | dict | unchanged | Citation/page reference |

**Invariant**: A card with `verified=true` MUST have non-empty `reviewer` and
`reviewed_at`. Enforced by `validate_card()` in `validator.py` (existing) and
by `kb.approve` tool (new).

## CitationGuardResult (existing entity — no changes)

Existing model in `services/safety/citation_guard.py`. F3 changes the *default
mode* from `warn` to `strict` at the integration point, not the dataclass.

| Field | Type | Notes |
|-------|------|-------|
| `ok` | bool | `true` = all numbers backed; `false` = violations found |
| `violations` | list[str] | Descriptions of unbacked numbers |
| `mode` | string | `"strict"` or `"warn"` |

**F3 behavior change**: When called from the report pipeline, `strict=True` is
always passed. The function's default parameter may remain `strict=False` for
backward compatibility in direct calls; the integration point forces `strict=True`.

## SafetyDecision (enhanced — existing entity)

Existing frozen dataclass in `services/safety/router.py`. F3 adds one optional
field.

| Field | Type | F3 Change | Notes |
|-------|------|-----------|-------|
| `route` | string | unchanged | Target report route |
| `safety_result` | dict | unchanged | Zone status, thresholds, values |
| `message` | str \| None | unchanged | Template message for yellow/red |
| `evidence_refs` | list[EvidenceRef] \| None | unchanged | Supporting data points |
| `recovery_check` | dict \| None | **NEW** | Optional recovery evaluation result |

**`recovery_check` structure** (when present):

```python
{
    "active": True,                          # recovery window was active
    "window_remaining_seconds": 4320,        # time left in the 2h window
    "original": {                            # the STORED earlier red-zone result (from a prior evaluate() call)
        "status": "red_zone",
        "reason": "glucose_red_zone_detected",
    },
    "recovery": {                            # the CURRENT evaluation on this (later) call
        "status": "clear",                   # or "yellow_zone" / "red_zone"
        "reason": "no_red_or_yellow_zone_points",
    },
    "recovery_confirmed": True,              # current eval is green/yellow
}
```

**Invariant (corrected — analyze D1)**: `recovery_check` is `None` when no red-zone
event is stored within the window for this user. When present, `original` is the
**stored earlier red-zone result** (NOT the current outer `safety_result`) and
`recovery` is the **current** evaluation; the outer `safety_result` equals
`recovery` (the current result). State is `_last_red_zone: dict[str, tuple[datetime, dict]]`
(in-memory, per-process). The inner re-eval uses a non-recursive `_evaluate_zone`
helper — `evaluate()` never recurses.

## ApprovalRecord (NEW entity)

A lightweight record of a clinical sign-off. Not persisted as a separate table;
stored as the card's `reviewer`/`reviewed_at` fields plus an audit log entry.

| Field | Type | Source |
|-------|------|--------|
| `card_id` | string | tool argument |
| `reviewer` | string | tool argument |
| `reviewed_at` | ISO-8601 string | tool argument or auto-generated |
| `previous_verified_state` | bool | always `false` (only unverified cards can be approved) |
| `approval_id` | string | `uuid4` (audit trail reference) |

**Lifecycle**: Created when `kb.approve` is invoked. The card is updated
atomically. An audit log entry records the approval with `approval_id`.

## SecurityFinding (NEW entity — documentation only)

Used in `sec-audit.md`. Not a runtime entity; purely for the security audit
artifact.

| Field | Type | Notes |
|-------|------|-------|
| `sec_id` | string | SEC-### identifier |
| `severity` | enum | LOW / MEDIUM / HIGH / CRITICAL |
| `owasp_category` | string | e.g., "LLM01: Prompt Injection" |
| `title` | string | Short description |
| `description` | string | Detailed finding |
| `current_mitigation` | string | What exists today |
| `recommended_action` | string | Proposed fix or follow-up |
| `references` | list[str] | Code locations, doc references |

## State / lifecycle notes

- No SQLite schema changes. All changes are to in-memory data structures and
  the KB JSON file (written only by `kb.approve`).
- The `SafetyRouter` adds `_last_red_zone` (in-memory `dict[str, tuple[datetime, dict]]`,
  not persisted). Process restart clears this state — safe default is no recovery check.
- The KB JSON is written only by `kb.approve`; the `assert_kb_readonly` guard
  is updated to allowlist `approve` while blocking all other mutators.
