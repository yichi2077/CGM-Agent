# CGM Agent Pre-Test Freeze - 2026-07-02

This document is the current source of truth for the 14-day Hermes virtual
simulation pre-test state. It supersedes older checklist rows when they
conflict.

## Dataset

- Default virtual data source:
  `examples/cgm_test_dataset/cgm_14d_1min.csv`
- Source file supplied for this freeze:
  `C:\Users\postgres\Desktop\新建文件夹 (4)\cgm_14d_1min_v2.csv`
- SHA256:
  `7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc`
- Shape: 20,010 rows, 14 days, native 1-minute points, one virtual AiDEX-style
  sensor.
- Metrics from the frozen CSV: min 59.3, max 204.5, mean 125.4 mg/dL, CV 16.5%,
  TIR 98.28%, TAR 1.48%, TBR 0.24%.
- Artifacts: compression-low window and sensor-noise window. The corrected v2
  CSV does not include the old dropout artifact.

## Required Runtime

- Hermes provider: `deepseek`
- Hermes model: `deepseek-v4-flash`
- Recommended invocation shape:

```powershell
hermes --provider deepseek --model deepseek-v4-flash -t cgm
```

`tests/test_hermes_e2e.py` defaults to this provider/model and can be overridden
with `HERMES_E2E_PROVIDER`, `HERMES_E2E_MODEL`, and `HERMES_E2E_BASE_URL`.

Verified on 2026-07-02: `python -m unittest discover -s tests` with
`HERMES_E2E_PROVIDER=deepseek` and `HERMES_E2E_MODEL=deepseek-v4-flash` ran
`494 tests OK`, including the live Hermes AIAgent E2E tool-call tests.

## Simulation DB

- Canonical Hermes-visible DB:
  `C:\Users\postgres\AppData\Local\hermes\cgm-agent\app.db`
- Baseline observed on 2026-07-02 before the v2 run:
  - `glucose_points`: 84 total.
  - `demo-prediabetes-14d` / `virtual:aidex`: 48 points.
  - `demo-prediabetes-sim` / `virtual:aidex`: 36 points.
  - Tool-layer check on the existing `demo-prediabetes-14d` window returned
    `timeseries.get_aggregate status=ok`, `point_count=48`, `data_coverage=100`,
    and `timeseries.get_realtime_snapshot status=ok`.
- To keep the corrected v2 run isolated from old partial runs, use:
  - `user_id=demo-prediabetes-14d-v2`
  - `source=virtual:aidex-v2`
- Before starting the 14-day run, verify:
  - DB exists and initializes cleanly.
  - `glucose_points` count is recorded as baseline.
  - `cgm_timeseries_get_aggregate` returns `status=ok` for the active window.
  - `cgm_timeseries_get_realtime_snapshot` returns `status=ok` and a latest
    glucose value after polling begins.

## Delivery Scope

Email is not a required feature for this 14-day simulation. Use `local_file` or
`webhook` as the delivery acceptance path. SMTP email can be tested separately as
an optional channel only.

## KB Scope

KB clinical verification is not a gate for this simulation. Unverified cards may
be used as test fixtures. Hermes must record every place where an answer or
report relies on unverified KB content.

## Known Non-Blocking Engineering Debt

- AGP percentile visualization remains deferred.
- `cli.py` and `reports/builder.py` are still large modules.
- The older checklist document contains stale rows; this freeze document and the
  current tests are authoritative for the simulation run.
