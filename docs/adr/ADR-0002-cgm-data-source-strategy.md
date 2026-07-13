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

## 2026-07-13 Update: Official AiDEX API implemented

MicroTech now publishes a LinX/AiDEX API Open Platform with an OAuth 2.0
authorization-code flow, sandbox and production environments, a data-range
resource, and an official sensor-glucose resource. The previous conditional
wording ("if MicroTech grants usable API credentials and documentation") is
therefore resolved at the protocol level.

The project now implements the official cloud path under `services/aidex/`:

- `aidex-auth` creates the official authorization URL, exchanges the one-time
  code, and stores access/refresh tokens encrypted in the canonical SQLite DB;
- `aidex-sync` reads `/v1/user/public/data-range` and
  `/v1/user/glu/sensor-glucose`, archives raw API rows, inserts deduplicated
  points, detects deterministic events, and hands new facts to memory;
- `--incremental` resumes from the latest stored AiDEX point with a bounded
  overlap, suitable for the Hermes `no_agent` cron entry at
  `scripts/hermes_cron/cgm_aidex_sync.py`;
- production data still requires a registered MicroTech developer application,
  production approval, and the user's explicit LinX authorization. Those are
  deployment credentials/consent, not missing project code.

Official contract sources:

- <https://aidexapi-x.microtechmd.com/doc/Overview>
- <https://aidexapi-x.microtechmd.com/doc/Authorization>
- <https://aidexapi-x.microtechmd.com/doc/Resource>

## 2026-07-13 Deployment correction: Android bridge is the production default

The target deployment does not have MicroTech developer/API entitlement.
Therefore protocol availability does not make the official API operationally
available, and the official API implementation is retained only as an optional
future adapter.

The current production order is now:

1. `LinX/AiDEX-X -> Android Juggluco -> authenticated LAN /sgv.json`;
2. xDrip-compatible Android output when that exact phone/sensor combination is
   already proven;
3. private HTTPS Nightscout as a remote relay when phone and PC are on different
   networks;
4. official AiDEX OAuth only if API entitlement is obtained later;
5. direct PC BLE remains non-mainline because Android already owns the supported
   device session and provides a documented bridge.

This correction supersedes later wording in this ADR that calls the official
AiDEX cloud API the default production direction. The hardware remains
MicroTech; only the transport changed.

## Context

The project already has SQLite storage, CSV/JSON import, Dexcom sync, metric computation, realtime snapshot reads, L0 context, reports, memory, safety, push/webhook, and 19 active Hermes-facing tools. The missing piece was a generic near-real-time Collector and practical adapters for CGM bridge feeds.

Dexcom code remains useful and tested, but it is not the project default. MicroTech/AiDEX is the default hardware direction and needs either vendor API access or a narrowly scoped direct-BLE feasibility study before it can become a production-quality PC collector.

## Decision

Collector v1 remains the xDrip/Juggluco/Nightscout-compatible HTTP bridge. The
default production direction is the authenticated Android Juggluco bridge:

- `xdrip` and `juggluco`: default poll path `/sgv.json`
- `nightscout`: default poll path `/api/v1/entries/sgv.json`
- internal calculation axis: `mg/dL`
- Collector execution: normal process, Windows Task Scheduler, or Hermes `no_agent` cron script
- no new LLM-facing Hermes tool for continuous collection

Continuous collection stays outside the LLM tool surface. Hermes reads the
result through the existing shared-DB `cgm_timeseries_*`, context, report, and
memory tools; its cron/no-agent runtime may trigger the deterministic sync.

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

Juggluco's current public documentation explicitly supports LinX/AiDEX-X and
the xDrip/Nightscout-compatible web interface. It is therefore the default
MicroTech transport for this deployment. The official API remains implemented
and contract-tested but inactive without entitlement. Direct PC BLE is not
required for the current path; Android BLE still needs real-device acceptance
for pairing, receipt latency, duplicate rate, disconnect recovery and 24-hour
stability.

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
