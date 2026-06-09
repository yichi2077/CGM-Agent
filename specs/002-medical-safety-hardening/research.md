# Phase 0 Research: Medical Safety Hardening (F3)

All decisions grounded in the current code (`citation_guard.py`, `router.py`,
`authoritative.py`, `validator.py`, `rag/tools.py`, `handlers/rag.py`,
`memory_guard.py`). Format: Decision / Rationale / Alternatives considered.

## R1 — Citation guard mode default and integration point

**Decision**: Change the default citation guard mode from `warn` to `strict`.
Wire `assert_authoritative_quotes` as a mandatory gate in the report generation
pipeline (`services/reports/builder.py`). The guard runs on the final generated
output text (post-generation), not on intermediate steps. When strict mode
returns `ok=false`, the builder halts report delivery and returns a standardized
"cannot confirm" response.

**Rationale**: In `warn` mode (current default), violations are logged but the
report is still delivered — the user sees potentially fabricated numbers. The
constitution (Principle I) requires zero tolerance for fabricated clinical data.
Running on final output prevents prompt-injection attacks that modify
intermediate context but cannot alter the final text that reaches the guard.

**Alternatives considered**:
- (a) Two-pass (warn then strict) — rejected: adds complexity with no behavioral gain; warn mode is only useful for debugging, which is a test concern.
- (b) Guard at RAG retrieval level — rejected: the guard must check what the model *generated*, not what was retrieved. A model could retrieve correct cards but still fabricate numbers in the narrative.
- (c) Prompt-only enforcement — rejected: violates Principle III (hard-coded safety routing).

## R2 — `kb.approve` tool design

**Decision**: New tool `kb.approve` registered alongside existing `rag.*` tools.
Arguments: `card_id` (string, required), `reviewer` (string, required),
`reviewed_at` (ISO-8601 string, optional — defaults to current UTC). The tool:
1. Loads the KB (or uses the cached instance).
2. Validates the card exists and `tier=curated`.
3. Sets `verified=true`, `reviewer`, `reviewed_at` atomically.
4. Writes the updated card back to the KB JSON file.
5. Returns the updated card as confirmation.

The `assert_kb_readonly` guard is updated to exempt the `approve` method
(allowlist pattern) while still blocking `add`, `write`, `insert`, `upsert`,
`update`, `delete`, `save`.

**Rationale**: The existing `validator.py` already enforces provenance for
`verified=true` cards. The missing piece is a tool to actually perform the
approval with provenance. Keeping it as a tool (not a CLI command) means it's
invocable through Hermes and auditable. The `tier=curated` restriction prevents
machine-ingested drafts from claiming authority.

**Alternatives considered**:
- (a) CLI-only approval — rejected: not auditable through the tool pipeline, not invocable from Hermes.
- (b) Batch approval — deferred: single-card approval is the MVP; batch can be added later.
- (c) Separate approval database — rejected: over-engineering for a single-user system; the KB JSON is the source of truth.

## R3 — Recovery double-check mechanism

**Decision (corrected — analyze D1)**: Add `_last_red_zone: dict[str, tuple[datetime, dict]]`
to `SafetyRouter` (keyed by user_id; stores the timestamp AND the red-zone result),
initialised in a new `__init__`. Extract the zone decision into a non-recursive
`_evaluate_zone(...)` helper; the public `evaluate()` calls it exactly once. On every
`evaluate()`:
1. `current = _evaluate_zone(...)`.
2. If `current` is red zone → store `_last_red_zone[user_id] = (now, current)`; `recovery_check=None`.
3. Else if a stored entry exists AND `now - stored_ts < RECOVERY_WINDOW_SECONDS` (default 7200) →
   attach `recovery_check = {active, window_remaining_seconds, original=stored_result, recovery=current, recovery_confirmed = current is green/yellow}`.
4. Else (window expired) → clear the stored entry; `recovery_check=None`.
`evaluate()` NEVER calls `evaluate()` from within itself.

The 2-hour window is a module-level constant `RECOVERY_WINDOW_SECONDS = 7200`
with an env-override `CGM_AGENT_RECOVERY_WINDOW_SECONDS`.

**Rationale**: The original draft re-evaluated the SAME data within one call, which
yields identical `original`/`recovery` (cannot detect recovery or relapse) and
recurses if it literally re-calls `evaluate()`. The corrected model compares the
**stored earlier red-zone result** against the **current** evaluation on a *later*
request — so when glucose has recovered, `recovery` returns green/yellow while
`original` retains the earlier red, and the report can show a genuine
"recovery confirmed" indicator. `SafetyRouter` was stateless (no `__init__`); the
added per-user state is in-memory and backward-compatible (frozen `SafetyDecision`
gains an optional `recovery_check` field).

**Alternatives considered**:
- (a) Persist red-zone timestamps in SQLite — rejected: adds DB schema changes for a single-user in-memory tracking need. If the process restarts, the worst case is a missed recovery check (safe default: no check, same as current behavior).
- (b) Require user confirmation — rejected: the spec says "system internal double-check, no user action needed."
- (c) Time-window in the report builder — rejected: the safety router is the correct layer for safety logic (Principle III).

## R4 — `assert_kb_readonly` exemption for `approve`

**Decision**: Update `assert_kb_readonly` in `memory_guard.py` to accept an
optional `allow_methods` parameter. When called from `AuthoritativeRAGService`,
pass `allow_methods={"approve"}`. All other mutator checks remain.

**Rationale**: The current guard checks for `add`, `write`, `insert`, `upsert`,
`update`, `delete`, `save`. The new `approve` method is a controlled write path
that requires clinical provenance — it's not a general mutator. An allowlist
approach is more explicit than removing the guard.

**Alternatives considered**:
- (a) Rename `approve` to avoid the checked names — rejected: obfuscation, not security.
- (b) Remove the guard entirely — rejected: violates Principle I.
- (c) Separate approval service — rejected: over-engineering; the allowlist is simpler and auditable.

## R5 — Security audit scope and format

**Decision**: Produce `sec-audit.md` covering OWASP LLM Top 10 categories
relevant to the CGM agent:
- **LLM01: Prompt Injection** — primary concern; mitigated by the citation guard running on final output (not on intermediate context that could be poisoned).
- **LLM06: Sensitive Information Disclosure** — mitigated by PHI encryption, audit log redaction, and the "cannot confirm" response not leaking card content.
- **LLM09: Excessive Agency** — mitigated by the hard-coded safety router, forced event provenance, and the KB read-only invariant.
- Additional categories (LLM04 Supply Chain, LLM10 Model Theft) noted but lower priority for this project.

Each finding gets a SEC-### ID, severity (LOW/MEDIUM/HIGH/CRITICAL),
description, current mitigation, and recommended actions.

**Rationale**: The constitution requires Damocles sign-off on Principles I, III,
and VII. The OWASP LLM Top 10 is the standard framework for LLM security
assessment. The audit is a documentation artifact that may spawn follow-up
tasks.

**Alternatives considered**:
- (a) Automated scanning only — rejected: LLM attack surface requires human judgment.
- (b) Broader scope (full OWASP Top 10) — rejected: the non-LLM OWASP items are standard web security, not specific to this feature.
