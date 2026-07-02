# ADR-0002: CGM Data Source Strategy

- **Status**: Accepted
- **Date**: 2026-06-27
- **Scope**: F2 data source direction and real-time collector implementation
- **Supersedes**: Open-ended "Libre/Nightscout/other source ADR" backlog item

## 2026-06-28 Clarification: Default CGM

The default target CGM for this project is MicroTech/AiDEX, not Dexcom. The xDrip/Juggluco/Nightscout-compatible HTTP collector remains useful as a generic bridge and compatibility path, but it is no longer the default data-source assumption for the user's primary hardware.

For MicroTech/AiDEX, the preferred next investigation order is:

1. `aidex_api` / vendor-cloud collector, if MicroTech grants usable API credentials and documentation.
2. `aidex_ble` direct-PC Bluetooth PoC for one exact AiDEX transmitter/sensor generation.
3. `xdrip` / `juggluco` / `nightscout` compatibility bridge only if the user later has an Android bridge or another compatible feed.

## Context

The project already has SQLite storage, CSV/JSON import, Dexcom sync, metric computation, realtime snapshot reads, L0 context, reports, memory, safety, push/webhook, and 18 active Hermes-facing tools. The missing piece was a generic near-real-time Collector and practical adapters for CGM bridge feeds.

Dexcom code remains useful and tested, but it is not the project default. MicroTech/AiDEX is the default hardware direction and needs either vendor API access or a narrowly scoped direct-BLE feasibility study before it can become a production-quality PC collector.

## Decision

The implemented Collector v1 data source is an xDrip/Juggluco/Nightscout-compatible HTTP feed:

- `xdrip` and `juggluco`: default poll path `/sgv.json`
- `nightscout`: default poll path `/api/v1/entries/sgv.json`
- internal calculation axis: `mg/dL`
- Collector execution: normal process, Windows Task Scheduler, or Hermes `no_agent` cron script
- no new LLM-facing Hermes tool for continuous collection

The implemented local command is:

```powershell
python -m hermes_cgm_agent source-poll --user-id user-1 --kind xdrip --url http://127.0.0.1:17580 --count 12
```

## Evidence Boundary

Public materials support the existence and interoperability direction of these routes:

- [xDrip](https://github.com/NightscoutFoundation/xDrip)
- [Juggluco web server](https://www.juggluco.nl/Juggluco/webserver.html)
- [Nightscout](https://nightscout.github.io/)
- [AiDEX guide](https://www.microtechmd.com/mtsc/uploads/multi_file/AiDEX_App_user_guide.pdf)
- [Yuwell DMS](https://en.yuwell-poctech.com/products/dms)

The xDrip/Juggluco/Nightscout HTTP interface is acceptable for backend implementation now, but it is a compatibility bridge rather than the default MicroTech/AiDEX path. AiDEX vendor API and direct-BLE behavior are not treated as implemented until a real-device runbook records pairing, receipt latency, duplicate rate, disconnect recovery, and 24-hour stability.

## Consequences

- `services/sources/` owns source parsing, HTTP URL policy, and single-poll orchestration.
- HTTP source URLs are HTTPS by default, or local/private HTTP only. Public plain HTTP requires explicit `CGM_SOURCE_ALLOW_INSECURE_HTTP=true`.
- Every poll creates an import batch and stores raw payload rows in `raw_cgm_records`.
- `glucose_points.timestamp` is measured-at time; `received_at` records collector/API receipt time.
- Deterministic detected glucose events are persisted in `detected_glucose_events`, separate from `user_events`.
- Hermes receives structured summaries, detected events, reports, and L0 context. It does not receive a continuous raw point stream.

## Validation

The implementation is covered by parser, fake HTTP integration, storage migration, realtime signal, CLI parser, repository, L0, and full-suite tests.

Current validation command:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests
```

Current result:

```text
Ran 476 tests
OK (skipped=1)
```
