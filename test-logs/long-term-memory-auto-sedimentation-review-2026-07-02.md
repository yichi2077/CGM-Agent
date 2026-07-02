# Long-Term Memory Auto-Sedimentation Review - 2026-07-02

## Request

Stop running processes, inspect why Hermes was not automatically sedimenting
CGM-derived long-term memory into L1/L2/L3 and warm summaries, repair the issue,
and review the result.

## Process State

Stopped the CGM simulation receivers and Hermes runtime processes that could
cache old modules:

- `external_receiver.py`
- `cgm_receiver.py`
- `virtual_cgm_feed.py`
- `auto_poll.py`
- `simulation_tick.py`
- `hermes gateway run`
- `hermes_cli.main serve`
- Hermes desktop processes that were respawning `hermes_cli.main serve`

Final process check for those patterns returned `remaining=none`.

## Findings

1. `SourcePollService.poll()` wrote raw batches, normalized glucose points, and
   deterministic `detected_glucose_events`, but it did not hand those derived
   facts to the memory repository.

2. The report path had a memory-candidate ingestion path, but the live CSV
   simulation path does not necessarily generate reports. Therefore ordinary
   realtime ingestion could leave L1/L2/L3 empty indefinitely.

3. Warm summary support existed through `ConsolidationService.synthesize_state()`
   and provider prefetch, but automatic scheduling was only connected to push
   emission and the manual `memory-synthesize` CLI. A live source poll with fresh
   points did not guarantee a warm summary.

4. Existing L0 context was working; the failure was in durable memory
   sedimentation, not DB ingestion or realtime analytics.

## Fix

Added `StreamMemoryService` in `src/hermes_cgm_agent/services/memory/stream.py`.

The service now performs the automatic sedimentation step after source polling:

- deterministic detected glucose events become idempotent L1 episodes;
- consolidation runs immediately after event-backed L1 creation, allowing repeat
  event types across distinct days to promote into L2 profile items and L3
  hypotheses using the existing thresholds;
- warm daily summaries are synthesized from recent aggregate metrics after new
  points arrive, with a default refresh throttle to avoid per-minute summary
  spam during normal streaming.

Wired this service into `SourcePollService` with `auto_memory_enabled=True` by
default and a configurable `warm_summary_min_interval_minutes`.

## Validation

Targeted tests:

```text
python -m unittest tests.test_source_poll tests.test_consolidation tests.test_warm_synthesis tests.test_l0_builder
Ran 16 tests - OK
```

Memory/tool integration tests:

```text
python -m unittest tests.test_memory_integration tests.test_memory_review tests.test_memory_tool_service tests.test_report_tool_service tests.test_hermes_plugin_integration tests.test_tool_registry tests.test_cli
Ran 78 tests - OK
```

Full suite:

```text
python -m unittest discover -s tests
Ran 496 tests - OK
```

Isolated end-to-end proof using a temporary SQLite DB:

```text
3 source-poll calls with deterministic data-gap events
L1 episodes: 3
L2 profile: pattern:data_gap, evidence_count=3
L3 hypothesis: Recurring data gap pattern, state=observing, evidence_count=3
Warm summaries: 3
```

## Review

No blocking issues found in the implemented path.

Residual behavior to be aware of:

- Normal glucose points do not each become L1 memory. This is intentional; raw
  time-series facts remain in `glucose_points`, while durable L1 is reserved for
  bounded, evidence-backed detected events.
- Warm summaries are throttled by default to one per hour unless a new detected
  event is sedimented. This prevents a one-minute receiver from creating tens of
  thousands of summary rows over a 14-day run.
- Existing Hermes processes had to be stopped so the next Hermes launch imports
  the repaired modules.
