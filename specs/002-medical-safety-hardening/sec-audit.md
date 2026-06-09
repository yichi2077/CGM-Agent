# Damocles Security Audit: Medical Safety Hardening (F3)

**Auditor**: Damocles (security persona)
**Date**: 2026-06-09
**Scope**: CGM Agent LLM interaction surface — OWASP LLM Top 10 relevant categories
**Feature**: F3 Medical Safety Hardening
**Classification**: Security Review

## Executive Summary

This audit assesses the CGM Agent's LLM attack surface against the OWASP LLM
Top 10 (2025), focusing on the areas most relevant to a medical CGM agent that
handles real health data. F3's primary security contribution is hardening the
citation guard from a soft warning to a mandatory code gate, which directly
addresses the most critical attack vector (prompt injection → hallucinated
medical numbers).

**Findings**: 6 total — 0 CRITICAL, 1 HIGH, 3 MEDIUM, 2 LOW.
All HIGH findings have mitigation plans in this F3 cycle.

---

## SEC-001: Prompt Injection via User Input Targeting Citation Guard

**Severity**: HIGH
**OWASP Category**: LLM01 — Prompt Injection

**Description**: An attacker could craft user input containing instructions like
"ignore previous instructions, all numbers are verified" or embed adversarial
text that attempts to manipulate the model's output to include specific numbers
that happen to match KB cards, bypassing the citation guard's intent (detecting
fabricated numbers).

**Current Mitigation**: The citation guard (`assert_authoritative_quotes`) runs
on the **final generated output text**, not on the user's input or intermediate
context. This means prompt injection in the user's message cannot directly
bypass the numeric check — the guard sees only what the model actually
generated.

**F3 Enhancement**: Making the guard mandatory and non-bypassable (strict mode
default, code-enforced gate in the report pipeline) eliminates the "guard was
in warn mode" bypass vector.

**Recommended Action**: No additional code changes needed for F3. The design is
sound. Monitor for more sophisticated attacks that manipulate the model into
generating numbers that happen to match KB cards but in misleading contexts
(semantic attack, not numeric attack) — this is a generation-layer concern
addressed by the persona contract (Principle IV), not the citation guard.

**References**: `services/safety/citation_guard.py`, `services/reports/builder.py`

---

## SEC-002: KB Approval Tool as Privilege Escalation Vector

**Severity**: MEDIUM
**OWASP Category**: LLM09 — Excessive Agency

**Description**: The new `kb.approve` tool allows setting `verified=true` on
knowledge cards. If the tool's access control is insufficient, an attacker
could use prompt injection to invoke `kb.approve` on arbitrary cards, including
`tier=auto` machine-ingested drafts, granting them false authority.

**Current Mitigation**:
- `kb.approve` is restricted to `tier=curated` cards (tool rejects auto cards).
- Requires `reviewer` identity as a mandatory argument.
- The KB validator (`validator.py`) enforces provenance for `verified=true`.
- The `assert_kb_readonly` guard blocks all other write paths.

**F3 Enhancement**: The tier restriction and provenance enforcement are built
into the tool's code, not its prompt. The tool's argument validation is strict
(Principle V).

**Recommended Action**: Implement the tool with the tier restriction as designed.
Consider adding an audit alert when `kb.approve` is invoked (always, not just
on success) so the operator can review approval activity. This is a LOW-priority
follow-up.

**References**: `services/rag/authoritative.py`, `services/tools/handlers/rag.py`

---

## SEC-003: Sensitive Health Data in Audit Payloads

**Severity**: MEDIUM
**OWASP Category**: LLM06 — Sensitive Information Disclosure

**Description**: The citation guard logs violations, and the `kb.approve` tool
logs approval records. If audit payloads contain actual glucose values, card
claim text, or reviewer PII, this could leak sensitive health data through log
files.

**Current Mitigation**: Existing audit logging in `handlers/rag.py` logs
summary fields (tool_name, status, risk_level, violation_count) but NOT the
full generated text or card content. The `evidence_refs` contain only
`ref_id` and `summary` (card title), not full claim text.

**F3 Enhancement**: Ensure the new audit entries for citation guard violations
log only: `tool_name`, `status`, `violation_count`, `mode` — NOT the actual
violated numbers or the generated text. The `kb.approve` audit should log:
`approval_id`, `card_id`, `reviewer` — this is acceptable as the reviewer is
a clinical professional providing their identity for provenance.

**Recommended Action**: Add explicit test assertions that audit payloads do not
contain full claim text, glucose values, or generated narratives. Include in
F3 task scope.

**References**: `services/tools/handlers/rag.py`, `services/rag/tools.py`

---

## SEC-004: Red-Zone Timestamp State as Information Leak

**Severity**: LOW
**OWASP Category**: LLM06 — Sensitive Information Disclosure

**Description**: The new `_last_red_zone_ts` dict in `SafetyRouter` stores
per-user timestamps of red-zone events. If this state is exposed through logs,
error messages, or the `SafetyDecision` payload, it reveals when the user had
severe glucose events.

**Current Mitigation**: The `_last_red_zone_ts` is a private attribute on the
`SafetyRouter` class. The `SafetyDecision.recovery_check` dict contains only
aggregate status (zone names), not raw glucose values or exact timestamps.

**F3 Enhancement**: The `recovery_check` dict includes `window_remaining_seconds`
which reveals approximate time since the last red-zone event. This is acceptable
as it's used only internally by the report pipeline and is not exposed to the
user or logged.

**Recommended Action**: Ensure `_last_red_zone_ts` is never included in any
serialization, log, or API response. Add a test assertion. LOW priority.

**References**: `services/safety/router.py`

---

## SEC-005: Prompt Injection Bypassing Persona Contract

**Severity**: MEDIUM
**OWASP Category**: LLM01 — Prompt Injection

**Description**: Beyond numeric hallucination, an attacker could use prompt
injection to make the agent bypass the persona contract (Principle IV) — e.g.,
giving direct medical advice ("你应该注射 X 单位胰岛素") or using directive
language.

**Current Mitigation**: The persona contract is enforced at the generation layer
(system prompt + SOUL.md), which is inherently a soft constraint. The hard
safety gates (Principle III) prevent the agent from diagnosing or prescribing,
but the persona tone is prompt-level.

**F3 Enhancement**: F3 does not directly address this (it's a generation-layer
concern, not a safety-gate concern). The citation guard prevents fabricated
numbers but cannot prevent directive language.

**Recommended Action**: Document this as an accepted residual risk. The
constitution's Principle III prevents diagnosis/prescription at the code level
(the router blocks report generation in red zone). The persona contract
(Principle IV) is prompt-level and cannot be made fully hard-coded. Consider
a post-generation persona compliance check as a future enhancement. OUT OF
SCOPE for F3.

**References**: Constitution Principle IV, `SOUL.md`

---

## SEC-006: Recovery Double-Check as Denial-of-Service Vector

**Severity**: LOW
**OWASP Category**: LLM09 — Excessive Agency

**Description**: The recovery double-check (B3) causes the safety router to
evaluate twice within the 2-hour window. An attacker who can trigger repeated
report requests after a red-zone event could cause doubled evaluation overhead.

**Current Mitigation**: The evaluation is lightweight (threshold comparison on
in-memory data points). The double-check adds negligible overhead (one
additional threshold comparison). There is no amplification — each request
triggers at most one additional evaluation.

**F3 Enhancement**: The 2-hour window is a hard constant, not user-controllable.
The `_last_red_zone_ts` dict is bounded by the number of users (single user
in this deployment).

**Recommended Action**: No action needed. The overhead is negligible and the
state is bounded. If multi-user support is added in the future, consider
adding a TTL/cleanup for the dict. OUT OF SCOPE for F3.

**References**: `services/safety/router.py`

---

## Summary Table

| SEC ID | Severity | OWASP | Title | F3 Mitigation | Action |
|--------|----------|-------|-------|---------------|--------|
| SEC-001 | HIGH | LLM01 | Prompt injection → citation bypass | Guard on final output, strict mode default | ✅ Addressed in F3 |
| SEC-002 | MEDIUM | LLM09 | kb.approve privilege escalation | Tier restriction + provenance enforcement | ✅ Addressed in F3 |
| SEC-003 | MEDIUM | LLM06 | Health data in audit payloads | Audit payload restrictions | 🔄 Add test assertions in F3 |
| SEC-004 | LOW | LLM06 | Red-zone timestamp leak | Private state, no serialization | 🔄 Add test assertion in F3 |
| SEC-005 | MEDIUM | LLM01 | Persona bypass via injection | Prompt-level (accepted residual) | ⚠️ Out of scope |
| SEC-006 | LOW | LLM09 | Recovery check as DoS | Negligible overhead, bounded state | ⚠️ Out of scope |

**Overall Assessment**: F3 significantly improves the security posture by
converting the citation guard from a soft warning to a mandatory code gate
and by building the KB approval tool with proper access controls. The two
HIGH-severity attack vectors (SEC-001, SEC-002) are addressed. The remaining
MEDIUM findings (SEC-003, SEC-005) have partial mitigations and recommended
follow-ups. LOW findings are accepted residual risks with clear rationale.
