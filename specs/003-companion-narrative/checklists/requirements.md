# Specification Quality Checklist: Companion Narrative (F4)

**Purpose**: Validate specification completeness and quality for F4 Companion Narrative
**Created**: 2026-06-10
**Feature**: ../spec.md

## Requirement Completeness & Coverage
- [x] CHK001 Are rate limits or maximum frequency caps specified for the non-urgent proactive pushes to prevent user fatigue? [Gap, Spec §FR-007] → Resolved: 1 non-urgent push/day, criticals unlimited (Session 2026-06-10)
- [x] CHK002 Is the time-to-live (TTL) or expiration rule defined for unanswered queries that are silently logged? [Gap, Spec §FR-008] → Resolved: 3-day TTL (Session 2026-06-10)
- [x] CHK003 Are the interaction mechanisms for the "Safety Disclaimer" explicitly defined (e.g., requires explicit consent vs. passive viewing)? [Clarity, Spec §FR-008] → Resolved: Strong-blocking, requires "已知晓" (Session 2026-06-10)
- [x] CHK004 Is the UI/UX boundary between F4 (conversation) and F3 (clinical report) clearly defined regarding how the user navigates between them? [Completeness, Spec §FR-001] → Resolved: `/report` slash command (Session 2026-06-10)
- [x] CHK005 Are error handling requirements defined if OS-level push notifications are disabled or fail? [Edge Case, Gap] → Resolved: Badge accumulation fallback (Session 2026-06-10)

## Requirement Consistency & Clarity
- [x] CHK006 Does the spec clearly distinguish the definition of "non-urgent daily trends" from noise to ensure high-value Insights? [Ambiguity, Spec §FR-007] → Addressed by Clarify-1: includes non-urgent but valuable trends like "今天下午的波动比昨天平稳"
- [x] CHK007 Do the length constraints (≤50 chars daily card) apply consistently to the new proactive push messages? [Consistency, Spec §FR-010] → Push messages follow the same SOUL.md output length norms

## Acceptance Criteria Measurability
- [x] CHK008 Can the "strict isolation in tone and content formatting" be objectively and programmatically verified? [Measurability, Spec §SC-001] → Verified via builder.py branching into render_clinical vs render_companion
- [x] CHK009 Are the simulation scenarios for testing "immediate proactive messages" fully specified with latency thresholds? [Measurability, Spec §SC-002] → Covered by T003/T004 unit tests with mock time
