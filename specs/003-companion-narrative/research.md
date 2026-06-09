# Research: Companion Narrative + Negotiated Interaction (F4)

**Date**: 2026-06-09
**Feature**: specs/003-companion-narrative/

## Decision 1: Narrative Template Strategy

**Decision**: Embed narrative templates as Python string literals / f-strings in `builder.py`, following the existing `_daily_card_text`, `_overview_section`, etc. pattern.

**Rationale**: The existing builder already has 15+ audience-branching narrative blocks using inline string construction. Introducing an external template engine (Jinja2, Mako) or file-based templates would be a new dependency and architectural pattern for a single file's concerns. The project prefers simplicity and offline capability (Constitution constraint).

**Alternatives considered**:
- External template files (YAML/JSON): rejected — adds I/O, harder to test, no existing pattern.
- Jinja2 templates: rejected — new dependency, overkill for ~200 lines of narrative.
- Separate `narrative.py` module: deferred to G1 (builder.py拆分); F4 keeps it in builder.py to minimize scope.

## Decision 2: Escalation State Derivation

**Decision**: Compute escalation level at report generation time by querying the scheduler's push_events table for consecutive anomaly days + reading L2ProfileItem for vulnerable flag. No new DB table.

**Rationale**: Escalation is a derived concept (how many consecutive days of anomaly → what concern level). The push_events table already records daily pushes with tier="daily". By querying recent daily push records that contain anomaly data, we can count consecutive anomaly days. The L2ProfileItem `vulnerable_population` key determines the timeline.

**Alternatives considered**:
- New `escalation_state` table: rejected — over-engineering for a derived concept; can be added later if persistence is needed.
- Real-time anomaly detection in report generation: considered — would duplicate scheduler logic; better to have scheduler pre-compute and report read.
- Memory-summary-based: rejected — escalation needs raw consecutive-day count, not synthesized state.

## Decision 3: Hypothesis Narrative Mapping

**Decision**: Add `_hypothesis_narrative` methods to `ReportService` (builder.py) that map HypothesisState to persona-compliant Chinese text. Called from `_patterns_section` and potentially `_observations_section`.

**Rationale**: The existing `_patterns_section` already generates pattern summaries but does not distinguish by hypothesis state. Adding state-aware narrative methods follows the existing `_daily_card_text` / `_event_type_label` pattern. The HypothesisState enum (CANDIDATE, OBSERVING, STABLE, ARCHIVED) is already imported in scheduler.py and available in the domain.

**Mapping**:
| State | Template Pattern | SOUL.md Reference |
|-------|-----------------|-------------------|
| CANDIDATE | "看起来可能有关，但还不够确定。要不要接下来多留意一下？" | §协商式假设验证 |
| OBSERVING | "过去几次里有N次类似，建议继续记录。" | §协商式假设验证 |
| STABLE | "在你的记录中，这个模式比较常见。" | §协商式假设验证 |
| ARCHIVED | "之前的规律最近不明显，先把它降级。" | §协商式假设验证 |

## Decision 4: Vulnerable Population Detection

**Decision**: Read `vulnerable_population` (boolean) from L2ProfileItem during escalation calculation. Fall back to standard timeline if key is missing.

**Rationale**: L2ProfileItem is the existing mechanism for storing distilled user beliefs (MEM-ARCH §5.1). A `vulnerable_population=true` key is a semantic belief about the user's medical category. Using L2 (not L1 or L3) is correct because this is a stable, verified belief — not an episode or hypothesis. The existing `ConsolidationService` can populate this during memory synthesis.

**Alternatives considered**:
- New `UserCategory` entity: rejected — adds schema for a single boolean flag; L2 key-value already handles this.
- Prompt-level detection: rejected — Constitution Principle III requires hard-coded routing; population type must be in data, not just in prompts.
- L1 episode-based: rejected — L1 is episodic, not a stable profile attribute.

## Decision 5: Output Length Enforcement

**Decision**: Enforce SOUL.md output length norms via Python string length checks in the narrative methods. If a generated narrative exceeds the norm, log a warning but do not truncate (preserve semantic completeness).

**Rationale**: SOUL.md defines: daily card ≤50 chars, weekly pattern ≤100 chars, general default ≤80 chars. These are soft guidelines, not hard limits. Truncating mid-sentence would break readability. A test-time assertion is more appropriate than runtime truncation.

**Enforcement approach**:
- **Test-time**: Each narrative test asserts `len(content) <= expected_max_chars`.
- **Runtime**: Log a warning if exceeded; do not truncate.
- **Luna review (DSG-###)**: Manual review of all templates for length compliance.
