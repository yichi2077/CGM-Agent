# Quickstart: Companion Narrative + Negotiated Interaction (F4)

**Date**: 2026-06-09
**Feature**: specs/003-companion-narrative/

## Prerequisites

- CGM-Agent project cloned and virtual environment activated
- All existing tests passing (374+ green baseline)
- Python ≥ 3.11

## Validation Scenarios

### SC-001: Narrative Quality — No Clinical Jargon in SELF Reports

```bash
# Run narrative template tests
python -m unittest tests/test_narrative_templates.py -v
```

**Expected**: All tests pass. Each test generates a report section with SELF audience and asserts:
- Content does NOT contain raw "TIR", "TAR", "TBR", "MBG", "CV", "GMI" as standalone acronyms
- Content uses life-language equivalents
- Content length ≤ 50 chars for daily card, ≤ 80 chars for general sections

### SC-002: Family Audience — One-Sentence Daily Card

```bash
# Run family audience tests
python -m unittest tests/test_narrative_templates.py -k family -v
```

**Expected**: Family daily cards are ≤1 sentence, contain no numbers, contain no clinical terminology.

### SC-003: Hypothesis State Narratives

```bash
# Run hypothesis narrative tests
python -m unittest tests/test_hypothesis_narrative.py -v
```

**Expected**: For each HypothesisState:
- CANDIDATE: contains hedged language + verification invitation
- OBSERVING: contains evidence count reference
- STABLE: contains confirmed pattern language (still hedged)
- ARCHIVED: contains demotion language

### SC-004: Escalation Concern at Correct Thresholds

```bash
# Run escalation tests
python -m unittest tests/test_escalation_concern.py -v
```

**Expected**:
- Day 1-2: Normal attribution language
- Day 3-4: Personal concern language ("你还好吗？")
- Day 5+: External support suggestion ("跟医生聊聊？")
- Vulnerable population: Same escalation but extra-gentle language

### SC-005: Red-Zone Suppression

```bash
# Run existing safety tests + new red-zone narrative test
python -m unittest tests/test_safety_router.py tests/test_narrative_templates.py -v
```

**Expected**: During red-zone override, no escalation or hypothesis narrative appears; only the safety message is rendered.

### SC-006: Full Suite Green

```bash
# Run entire test suite
python -m unittest discover -s tests
```

**Expected**: 374+ tests pass, no regressions, new F4 tests included in count.

## End-to-End Validation

After implementation, run the full pipeline:

```bash
# 1. Generate a daily report with known data
python -c "
from hermes_cgm_agent.services.reports.builder import ReportService
# ... (use existing test fixtures)
# Verify output uses life-language
"

# 2. Generate weekly report with hypothesis data
# Verify hypothesis state-appropriate language

# 3. Simulate 5 days of anomaly + generate report
# Verify escalation concern language appears
```

## Notes

- All validation scenarios are automated tests — no manual steps required.
- Luna DSG-### review is a manual design quality gate separate from these tests.
