# Default Virtual CGM Test Dataset

This directory contains the default engineering fixture for CGM-Agent local E2E
validation. The fixture is synthetic and deterministic. It is designed to prove
that the software pipeline runs without real patients or a real CGM device; it is
not clinical evidence and must not be used to validate medical algorithm
efficacy.

## Default Files

- `generate_cgm_dataset.py`: deterministic generator.
- `cgm_14d_1min.csv`: one user, 14 days, native 1-minute CGM points. The
  current default file is the corrected v2 fixture
  (`csv_sha256=7e51d95a9a26a38e8fae45e4d9e7d8daa50ce9887f999986eb58aa0efdaa0edc`).
- `behavior_events_14d.json`: meals, post-meal walks, stress windows, and poor sleep notes that drive the synthetic glucose curve.
- `manifest.json`: fixture metadata, artifact list, and summary metrics.
- `virtual_cgm_feed.py`: local xDrip/Nightscout-style HTTP feed for source-poll E2E tests.
- `auto_poll.py`: local unattended runner that repeatedly polls the feed into the Hermes CGM database.
- `simulation_tick.py`: one-shot resumable runner for Windows Task Scheduler or cron.
- `cgm_3x14.csv`: legacy 3-sensor, 5-minute fixture retained for older tests and comparisons.

## Current Fixture Shape

- User profile: prediabetes-style / impaired glucose tolerance, with mildly high fasting baseline and postprandial excursions.
- Resolution: 1-minute native points; default simulated upload emits one point every 5 minutes.
- Duration: 14 days, one virtual AiDEX-style sensor.
- Artifacts: 2-hour sensor warmup gap, one compression-low window, and one short sensor-noise window.
- Behavior drivers: breakfast/lunch/dinner carbs, post-dinner walks, stress afternoons, and poor sleep.
- Summary metrics for the corrected v2 fixture: min 59.3, max 204.5, mean
  125.4 mg/dL, CV 16.5%, TIR 98.28%, TAR 1.48%, TBR 0.24%.

Regenerate the default fixture:

```powershell
python examples/cgm_test_dataset/generate_cgm_dataset.py
```

## Local Network Feed

Start a feed that exposes the 1-minute fixture as 5-minute device uploads:

```powershell
python examples/cgm_test_dataset/virtual_cgm_feed.py --emit-interval-min 5
```

Poll one emitted point into the local SQLite database:

```powershell
python -m hermes_cgm_agent source-poll --user-id demo-prediabetes-user --kind xdrip --url http://127.0.0.1:17580 --count 1 --source virtual:aidex --expected-interval-min 5
```

Run a short unattended smoke poll:

```powershell
python examples/cgm_test_dataset/auto_poll.py --user-id demo-prediabetes-user --url http://127.0.0.1:17580 --count 12 --interval-sec 0 --max-polls 3
```

Run the real-time 14-day simulation poller:

```powershell
python examples/cgm_test_dataset/auto_poll.py --user-id demo-prediabetes-user --url http://127.0.0.1:17580 --count 1 --interval-min 5 --duration-hours 336
```

For a scheduler-friendly 14-day run, execute one tick every 5 minutes. Each tick
derives the next feed index from SQLite, so it can resume after shell exits or
machine sleep:

```powershell
python examples/cgm_test_dataset/simulation_tick.py --user-id demo-prediabetes-user
```

For a MicroTech/AiDEX-X-style 1-minute upload simulation, run the same feed with
`--emit-interval-min 1` and poll with `--expected-interval-min 1`.
