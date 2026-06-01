# G0-G7 Demo

This folder is a manual smoke dataset for the G0-G7 hardening path.

Use it with a disposable database:

```powershell
$env:PYTHONPATH='src'
$env:CGM_AGENT_DB_PATH='.runtime/demo_g0_g7.db'
```

The full command sequence is documented in:

`C:\Users\postgres\Desktop\新建文件夹 (4)\dveps\docs\hermes-cgm-agent-dev-plan\G0_G7_HARDENING_RUNBOOK.md`

## Fixtures

- `cgm_14d.csv` / `cgm_14d.json`: sparse 28-point smoke fixture (fast import checks).
- `cgm_14d_realistic.csv`: deterministic ~4,032-point, 5-minute-cadence 14-day fixture with
  circadian baseline, postprandial spikes, two overnight lows, and one sensor gap. Use it to
  validate LBGI/HBGI, coverage, and `GlucoseEvent` detection against lifelike density.
  Regenerate with:

  ```powershell
  python examples/g0_g7_demo/generate_realistic_cgm.py
  ```

  Importing it yields ~99% coverage and a realistic spread of detected glucose events
  (hyper from meals, two overnight lows at alert severity, rapid rise/fall, one data gap).

