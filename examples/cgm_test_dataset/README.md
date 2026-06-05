# Multi-sensor CGM test dataset (3 × 14 days)

A deterministic, reproducible CGM dataset for exercising the whole system
(import → normalize → analytics → events → reports → memory) without needing
Dexcom/Libre credentials or a live API.

## Why synthetic

Public CGM datasets — [ShanghaiT1DM](https://www.nature.com/articles/s41597-023-01940-7)
(~3–14 days/patient), Hall 2018 (~10 days), and the
[Awesome-CGM](https://github.com/IrinaStatsLab/Awesome-CGM) collection — provide
roughly a single ~14-day sensor wear per subject. Getting "30 days (3×14)" for one
person means composing several sensor sessions anyway, so this generator emits a
controlled signal with known, checkable features instead.

## Files

- `generate_cgm_dataset.py` — the generator (seeded, parameterized).
- `cgm_3x14.csv` — 11,988 points, 5-min cadence, 3 sensors × 14 days (42 days).
- `manifest.json` — per-sensor session boundaries + metadata.
- `report_stress_week.json`, `report_recovery_week.json` — sample `reports.generate` inputs.

## What it exercises

Three distinct 14-day sensor sessions (separate `device_id`s), with a cross-sensor
story so trend/report output is meaningful:

| Sensor | Days | Story | Expected weekly report |
|--------|------|-------|------------------------|
| A `SENSOR-A-7F3C` | 0–13  | decent control | mean ~120s, a couple nocturnal lows |
| B `SENSOR-B-2A91` | 14–27 | stress/holiday week | mean ~157, TIR-above ~20%, 170 events, 1 data gap |
| C `SENSOR-C-5E08` | 28–41 | recovery | mean ~112, ~100% in range |

Built-in features the pipeline detects: a ~2h warmup gap at each sensor change,
one ~3h mid-sensor dropout (sensor B day 5), 3 postprandial spikes/day, nocturnal
lows on specific days. Timestamps are **naive local** (import with
`--timezone Asia/Shanghai`).

## Regenerate

```bash
python examples/cgm_test_dataset/generate_cgm_dataset.py
# exactly 30 days instead of 3×14=42:
python examples/cgm_test_dataset/generate_cgm_dataset.py --days-per-sensor 10
```

## Run it through the system

```bash
# import + normalize (gap detection)
CGM_AGENT_DB_PATH=.runtime/test_30d.db PYTHONPATH=src \
  python -m hermes_cgm_agent import-cgm \
  --file examples/cgm_test_dataset/cgm_3x14.csv --format csv \
  --user-id demo-user --timezone Asia/Shanghai

# generate a weekly report (try both the stress and recovery windows)
CGM_AGENT_DB_PATH=.runtime/test_30d.db PYTHONPATH=src \
  python -m hermes_cgm_agent tool-call reports.generate \
  --input examples/cgm_test_dataset/report_stress_week.json --session-id test-30d
```

Verified run: import reports `inserted_point_count: 11988, missing_range_count: 3`;
the stress-week report shows mean 156.61 mg/dL, 20.15% above range, 170 detected
events; the recovery-week report shows ~100% in range.

## Option B — feed it through the Dexcom API path (mock server)

To exercise the real Dexcom ingest pipeline (OAuth → token store → `dataRange` →
chunked, time-ordered EGV pulls → mapper → dedup) without the region-blocked
Dexcom sandbox, `mock_dexcom_server.py` serves this same CSV in Dexcom v3 EGV
format. Point the real client/CLI at it with `DEXCOM_BASE_URL`.

```bash
# 1) start the mock API (serves cgm_3x14.csv as Dexcom v3 EGVs)
python examples/cgm_test_dataset/mock_dexcom_server.py        # http://127.0.0.1:8473

# 2) point the real CLI at it + dummy creds, authorize, then poll periodically
export DEXCOM_CLIENT_ID=mock DEXCOM_CLIENT_SECRET=mock
export DEXCOM_BASE_URL=http://127.0.0.1:8473
export CGM_AGENT_DB_PATH=.runtime/test_dexcom.db
PYTHONPATH=src python -m hermes_cgm_agent dexcom-auth --user-id demo-user --code mock-code

# each call pulls the next 7-day window in chronological order (streaming cursor);
# re-running a window is idempotent (repository dedups). Schedule via cron/hermes.
for i in 1 2 3 4 5 6; do
  PYTHONPATH=src python -m hermes_cgm_agent dexcom-sync --user-id demo-user --days 7
done
```

With `DEXCOM_MOCK_STREAM=0` the server exposes the full 42-day range at once, so a
single `dexcom-sync --days 45` pulls everything in 7-day chunks. The points land
in `glucose_points` with `source = dexcom:sandbox`, so the same reports/analytics
above work on Dexcom-ingested data too.

### Field richness

EGV records carry the consumed Dexcom v3 fields with real values — `value`,
`systemTime`, `displayTime`, `unit`, `trend`, **`trendRate`** (mg/dL/min),
`transmitterId`, `transmitterTicks`, `transmitterGeneration`, `displayDevice`,
`recordId`, and `status` (null on normal readings, set on clamp extremes, as on
the real API).

The mock serves the **full Dexcom "life data" surface** on `/v3/users/self/events`
— all six event categories with subtypes — so `dexcom-sync` exercises every
mapper branch:

| Dexcom eventType (subType) | → project UserEvent | payload |
|---|---|---|
| carbs | meal | `carbs_grams` |
| insulin (fastActing / longActing) | medication | `insulin_units`, `subtype` |
| exercise (light / medium / heavy) | exercise | `duration_minutes`, `ts_end`, `subtype` |
| health (stress / illness / …) | symptom | `subtype` |
| bloodGlucose | note | `blood_glucose`, `unit` |
| notes | note | free text |

Verified single full pull: EGV `inserted=11988` (= all CSV rows; the 5 `dup` are
inclusive chunk-boundary overlaps the repository dedups), events `inserted=369,
skipped=0` mapping to meal 126 / medication 168 / exercise 18 / symptom 12 /
note 45. The egvs/events window is inclusive of `endDate` so the final reading on
`dataRange.end` is never dropped.
