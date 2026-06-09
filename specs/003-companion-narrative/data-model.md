# Data Model: Companion Narrative + Negotiated Interaction (F4)

**Date**: 2026-06-09
**Feature**: specs/003-companion-narrative/

## Entities (Existing — No Schema Changes)

### L3Hypothesis (domain/memory.py)

Already has the state machine needed for C2.

| Field | Type | Notes |
|-------|------|-------|
| hypothesis_id | str | Primary key |
| user_id | str | Owner |
| statement | str | The hypothesis text |
| state | HypothesisState | CANDIDATE / OBSERVING / STABLE / ARCHIVED |
| evidence_count | int | Times evidence supporting this hypothesis |
| contra_count | int | Times evidence contradicting |
| evidence_refs | list[EvidenceRef] | Supporting evidence |
| last_checked | datetime | Last verification time |
| created_at | datetime | Creation time |
| updated_at | datetime | Last update |

**F4 usage**: Report builder reads `state` and `evidence_count` to select narrative template.

### L2ProfileItem (domain/memory.py)

Already has key-value storage needed for C3 vulnerable flag.

| Field | Type | Notes |
|-------|------|-------|
| item_id | str | Primary key |
| user_id | str | Owner |
| key | str | Profile key (e.g., "vulnerable_population") |
| value | dict | {"value": true, "condition": "pregnancy"} |
| confidence | float | 0-1, how confident this belief is |
| evidence_count | int | Supporting evidence count |
| is_active | bool | Whether currently active |

**F4 usage**: Scheduler reads `key="vulnerable_population"` to determine escalation timeline.

### ReportAudience (domain/report.py)

| Value | Label | F4 Narrative Style |
|-------|-------|-------------------|
| SELF | 用户版 | Conversational Chinese, life-language, 30-80 chars |
| CLINICIAN | 医生版 | Clinical language, structured numbers, no narrative polish |
| FAMILY | 家属版 | Simplest language, one sentence for daily, no numbers |

### HypothesisState (domain/memory.py)

| Value | Chinese Label | Narrative Template |
|-------|--------------|-------------------|
| CANDIDATE | 候选 | Hedged + invitation ("看起来可能有关…要不要多留意？") |
| OBSERVING | 观察中 | Evidence-counted ("过去N次里有M次类似…") |
| STABLE | 较稳定 | Confirmed pattern ("在你的记录中，这个模式比较常见") |
| ARCHIVED | 归档 | Demotion ("之前的规律最近不明显") |

## Derived Concepts (Not Persisted)

### EscalationState (computed at report generation time)

| Level | Condition | Narrative Effect |
|-------|-----------|-----------------|
| NORMAL | consecutive_anomaly_days < 3 | Normal data attribution |
| CONCERN | 3 ≤ consecutive_anomaly_days < 5 | Personal concern ("最近几天都有点波动，你还好吗？") |
| EXTERNAL_SUPPORT | consecutive_anomaly_days ≥ 5 | External support suggestion ("要不要跟医生聊聊？") |

**For vulnerable populations** (L2 `vulnerable_population=true`):
- Same thresholds (1/3/5 per SOUL.md) but the language is extra gentle
- Day 1 already gets heightened awareness (more frequent check-ins)

**Derivation logic**:
```
escalation_level = (
    EXTERNAL_SUPPORT if consecutive_days >= 5
    else CONCERN if consecutive_days >= 3
    else NORMAL
)
```

### ConsecutiveAnomalyDays (computed in scheduler)

Not a persisted entity. Computed by `PushSchedulerService` during `push_tick`:
1. Query recent `push_events` for the user (daily tier, last 7 days)
2. For each push, check if the associated metrics indicate anomaly (TAR > 0 or TBR > 0 or warnings present)
3. Count consecutive days ending today with anomaly

Store the count in the `push_tick` result dict for downstream consumption by the report builder.

## Narrative Matrix (per report section × audience)

| Section | SELF | CLINICIAN | FAMILY |
|---------|------|-----------|--------|
| daily_card | Conversational, ≤50 chars | Clinical, TIR/TAR/TBR | One sentence, no numbers |
| overview | Life-language coverage | Structured data coverage | Simple reassurance |
| metrics | TIR→"范围里的时间", TAR→"偏高的时候" | Raw percentages | "大部分时间都挺好" |
| observations | Pattern in life terms | Clinical observation | "今天整体还行" |
| follow_up | Gentle invitation | Clinical note | Simple reminder |
| patterns | Hedged + state-aware | Evidence summary | N/A (weekly only) |
| doctor_appendix | Summary with numbers | Full structured data | "已经整理好了" |
| escalation_concern | Progressive care | Clinical escalation note | N/A |
