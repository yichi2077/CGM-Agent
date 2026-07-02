# Quickstart: CGM Simulation Pipeline

## Local Max-Speed Run

```powershell
python -m hermes_cgm_agent.cli simulate --max-speed --time-base original
```

Expected:

- Exit code 0.
- A new `.runtime/simulation/<timestamp>/app.db`.
- `simulation_report.json` and `simulation_report.md`.

## Three-Day Smoke

```powershell
python -m hermes_cgm_agent.cli simulate --max-speed --days 3 --out-dir .runtime/simulation/smoke-3d
```

## Hermes Preflight

```powershell
python -m hermes_cgm_agent.cli simulate --max-speed --days 1 --time-base shift-to-now --hermes
```

If the installed Hermes runtime is unavailable in the current Python
environment, the command returns exit code 2 and writes `hermes_stage.json`.

## Canonical Hermes DB Run

Use this only when intentionally writing into the Hermes-visible store:

```powershell
python -m hermes_cgm_agent.cli simulate `
  --acceleration 300 `
  --time-base shift-to-now `
  --db-path "$env:LOCALAPPDATA\hermes\cgm-agent\app.db" `
  --hermes
```
