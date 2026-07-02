# Data Model: CGM Simulation Pipeline

## ReplayRecord

- `sim_ts`: simulated UTC timestamp for the reading.
- `record`: source `RawCGMRecord`.
- `reading_index`: 1-based replay index.

## StreamIngestResult

- `inserted`: number of glucose points inserted for a reading.
- `duplicate`: number of duplicate points skipped.
- `issues`: normalization or import issues.

## SimulationRunResult

- `status`: `ok` or `failed`.
- `exit_code`: 0 for success, 1 for invariant/runtime failure.
- `run_id`
- `out_dir`
- `db_path`
- `emitted`
- `inserted`
- `duplicate`
- `issues`
- `report_json`
- `report_md`
- `stage_counts`

## SimulationIssue

- `stage`
- `sim_now`
- `reading_index`
- `message`
- `traceback`
