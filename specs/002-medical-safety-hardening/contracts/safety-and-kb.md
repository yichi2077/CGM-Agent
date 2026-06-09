# Phase 1 Contracts: Medical Safety Hardening (F3)

Behavioral contracts the implementation must satisfy. These drive the tasks and
the regression tests.

## C1 — `rag.verify_quotes` strict gate contract

**Default mode**: function default stays `strict=False` (backward-compatible for the `rag.verify_quotes` tool + `test_rag`); `strict=True` is forced **only** at the report-pipeline integration point (analyze N1).

**Signature (verified against code)**: `assert_authoritative_quotes(documents, generated_text, *, strict=False)` — `documents` is the FIRST positional arg. Callers MUST pass `documents` then `generated_text` (analyze C1; the prior draft swapped them, which would silently no-op the guard).

**Behavior**:
- Input: `documents` (list of retrieved authoritative card dicts), `generated_text` (string — the **medical-claim/guidance narrative only**, NOT the user's own deterministic metric sections, which are Principle-I-clean from `CGMAnalyticsService`; analyze I2/I3).
- Backing set: retrieved authoritative cards regardless of `verified` this cycle (verified-only DEFERRED until clinical sign-off; analyze I2). Unverified cards still carry the `[待核验]` marker.
- For each significant number in `generated_text`, check if it appears in any
  card's `claim_en`, `claim_zh`, or `text` field.
- If any number is unbacked AND `strict=True`: return `ok=false` with violations.
- If `strict=False`: return `ok=true` with violations logged (warn mode).
- Empty/whitespace text → `ok=true`, no violations.

**Integration contract** (in report builder):
- The guard MUST run on the final generated medical-claim narrative, with `assert_authoritative_quotes(documents, generated_text, strict=True)` (correct positional order).
- When `ok=false` in strict mode, the builder MUST halt delivery and return a
  "cannot confirm" response: `"这个问题涉及的医学数据我无法确认准确性。我可以帮你整理原始数据，复诊时带给医生。需要我生成数据摘要吗？"`
- The guard MUST NOT be skippable via configuration, prompt instruction, or
  any code path that bypasses the builder.

**Tests**: `tests/test_citation_guard.py`
- Backed numbers → `ok=true`.
- Unbacked numbers + strict → `ok=false`.
- Unbacked numbers + warn → `ok=true` (violations logged).
- Empty text → `ok=true`.
- Prompt injection in user input → guard runs on output, not input.
- No verified cards in KB → all numbers unbacked → `ok=false` in strict.

## C2 — `kb.approve` tool contract

**Tool name**: `kb.approve`
**Arguments** (strict JSON-boundary validation):

```jsonc
{
  "card_id": "string",       // required — must reference an existing card
  "reviewer": "string",      // required — clinical reviewer identity
  "reviewed_at": "string"    // optional — ISO-8601 timestamp, defaults to now
}
```

**Behavior**:
1. Validate arguments (strict — no coercion, no lenient matching).
2. Load KB; find card by `card_id`. If not found → error.
3. If `card.tier != "curated"` → error: "Only curated cards can be approved. This card is tier=auto."
4. If card already `verified=true` with same `reviewer` → idempotent no-op, return current state.
5. Set `verified=true`, `reviewer`, `reviewed_at` (default: current UTC ISO-8601).
6. Write updated card back to KB JSON.
7. Log audit event: `approval_id` (uuid), `card_id`, `reviewer`, `reviewed_at`.
8. Return: `status="ok"`, `payload.card_id`, `payload.verified=true`, `payload.reviewer`, `payload.reviewed_at`.

**Error cases**:
- `card_id` not found → error response with clear message.
- `tier=auto` → error with explanation.
- Missing required arguments → strict validation error (no coercion).
- KB file not writable → error with clear message.

**Tests**: `tests/test_kb_approve.py`
- Happy path: approve a curated card → `verified=true`, provenance set.
- Idempotent: re-approve same card + same reviewer → no-op, returns current state.
- Tier restriction: approve an auto card → rejected.
- Missing card → error.
- Missing required args → validation error.
- KB validator confirms approved card passes.

## C3 — `SafetyRouter.evaluate` recovery contract

**New behavior** (backward-compatible). **Corrected design (analyze D1)** — the prior draft ("record now's red ts, then run a second `evaluate()` on the same data") was broken: same-data-twice yields identical original/recovery and a nested `evaluate()` recurses. The correct model compares a stored EARLIER red result against the CURRENT result on a LATER call:
- `SafetyRouter` tracks `_last_red_zone: dict[str, tuple[datetime, dict]]` (timestamp + the red zone result), initialised in a new `__init__`.
- Extract the zone decision into a non-recursive helper `_evaluate_zone(points, scope) -> dict`. The public `evaluate()` calls `_evaluate_zone` exactly once.
- On `evaluate()`:
  1. `current = _evaluate_zone(...)` (single, non-recursive).
  2. If `current` is red zone → store `_last_red_zone[user_id] = (now, current)`; `recovery_check = None`.
  3. Else if a stored entry exists AND `now - stored_ts < RECOVERY_WINDOW_SECONDS` → attach
     `recovery_check = {active: True, window_remaining_seconds, original: stored_result, recovery: current, recovery_confirmed: current is green/yellow}`.
  4. Else (window expired) → clear the stored entry; `recovery_check = None`.
  5. The router NEVER calls `evaluate()` from within `evaluate()`.

**`SafetyDecision.recovery_check`**:
- `None` when no recovery window is active.
- Dict with `active`, `window_remaining_seconds`, `original`, `recovery`,
  `recovery_confirmed` when active.

**Constants**:
- `RECOVERY_WINDOW_SECONDS = 7200` (2 hours).
- Env override: `CGM_AGENT_RECOVERY_WINDOW_SECONDS`.

**Backward compatibility**: Existing code that reads `SafetyDecision` without
accessing `recovery_check` is unaffected (the field defaults to `None`).

**Tests**: `tests/test_safety_router.py`
- Red zone → recovery window active → second evaluation within window.
- Red zone → second evaluation after window expires → no recovery check.
- Green zone → no recovery check (no red-zone history).
- Red zone → recovery eval is also red → `recovery_confirmed=false`.
- Red zone → recovery eval is green → `recovery_confirmed=true`.
- Window boundary: exactly at `RECOVERY_WINDOW_SECONDS` → no recovery check.

## C4 — Report pipeline integration contract

**Behavior**:
- `ReportBuilder.generate()` MUST call `assert_authoritative_quotes` with
  `strict=True` on the final generated text before delivering the report.
- If the guard returns `ok=false`, the builder MUST:
  1. NOT deliver the original report.
  2. Return the "cannot confirm" response (persona-aligned, gentle).
  3. Log the violation to the audit service.
- If the guard returns `ok=true`, proceed normally.

**Tests**: `tests/test_report_pipeline.py`
- Report with backed numbers → delivered.
- Report with unbacked numbers → blocked, "cannot confirm" response.
- Audit log records the violation.

## Cross-cutting (constitution)

- No clinical numbers produced by the model (Principle I) — enforced by C1.
- KB read-only except through `kb.approve` with provenance (Principle I) — enforced by C2 + `assert_kb_readonly` update.
- Hard-coded safety routing (Principle III) — enforced by C3.
- Full test suite green; new guards added (Principle V).
- DECISION_LOG entry for citation-gate-mode and recovery-window decisions (Principle VI).
