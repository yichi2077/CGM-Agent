# Implementation Plan: Companion Narrative + Negotiated Interaction (F4)

**Feature Branch**: `003-companion-narrative`
**Status**: Approved
**Spec Reference**: [spec.md](./spec.md)

## 1. Technical Context

This plan details the implementation of F4, transforming the CGM agent into an "Informed Companion". It requires modifying the state/memory models to handle interaction lifecycles, upgrading the scheduler for proactive pushes, isolating the report builder, and adding command hooks.

### Core Architecture Components Involved
- **Domain Models**: `src/hermes_cgm_agent/domain/memory.py` (New state entities)
- **Scheduler**: `src/hermes_cgm_agent/services/scheduler.py` (Push logic, limits)
- **Report Rendering**: `src/hermes_cgm_agent/services/reports/builder.py` (Isolation)
- **Narrative Logic**: `src/hermes_cgm_agent/services/reports/narrative_templates.py` (New module)
- **CLI/Command**: `src/hermes_cgm_agent/cli.py` or equivalent interaction router (for `/report`)

## 2. Constitution Check

Evaluating against `constitution.md`:
- **I. Medical Zero-Tolerance & Authoritative Read-Only**: ✅ Pass. Narrative templating does not alter any computed metrics. F3 isolation ensures clinical data is never chat-washed.
- **II. Dual-Track Memory Isolation**: ✅ Pass. F4 deals strictly with L3 hypothesis rendering and L2 profile reading (for vulnerable populations).
- **III. Hard-Coded Safety Routing**: ✅ Pass. `FR-009` guarantees that red-zone safety override suppresses any companion narrative.
- **IV. Informed-Companion Persona Contract**: ✅ Pass. This is the primary goal of F4. All templates will use hedged language, question-guided exploration, and non-judgmental tone.
- **V. Test-First & Green CI Gate**: ✅ Pass. Automated tests for builder isolation, rate-limiting, and TTLs are required in the task list.
- **VI. Traceable Decisions**: ✅ Pass. F3/F4 strict isolation and Push mechanics are documented in the Spec and this Plan.
- **VII. Hermes Boundary & Data Privacy**: ✅ Pass. Utilizing OS push failovers (badge accumulation) keeps the implementation cleanly behind Hermes interfaces.

*Gate Status: GREEN*

## 3. Data Model Changes (Phase 1)

See `data-model.md` (to be generated) for full fields.
- `PendingInteraction`: Tracks unanswered active interactions with a 3-day TTL.
- `EscalationState`: Enum mapping consecutive anomaly days + vulnerability to response levels.

## 4. Phase 0: Research / Dependencies
No outstanding unknowns. The OS Push fallback will use the existing Hermes Agent internal state to inject red-dot badges on the next CLI/App start.

## 5. Next Steps
Move to Task execution:
1. Implement `PendingInteraction` and `EscalationState`.
2. Update `PushSchedulerService` for rate limits and non-urgent insights.
3. Extract `narrative_templates.py`.
4. Refactor `builder.py` for F3/F4 physical isolation + vulnerable safety disclaimer.
5. Expose `/report` slash command.
6. Write Tests.
