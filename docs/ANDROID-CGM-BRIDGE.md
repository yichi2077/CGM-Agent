# Android CGM Bridge Runbook

## Chosen production route (AiDEX X — xDrip Companion mode)

```text
AiDEX X sensor
  -> AiDEX vendor app (owns the BLE connection, UI, and hypo/hyper alarms)
  -> Android system notifications (glucose value, ~1 min)
  -> xDrip+ Companion App mode (captures notifications; touches no Bluetooth)
  -> xDrip web service on the trusted home LAN
  -> cgm_bridge_poll.py (Hermes no-agent cron)
  -> canonical app.db
  -> analytics, detected events, memory, F4 narrative, F5 push, Hermes CGM tools
```

**This project is an AI enhancement layer over the AiDEX vendor app, not a
replacement (D064).** The vendor app keeps doing everything it does today —
live values and, critically, the real-time hypo/hyper **alarms**. xDrip+ runs
in Companion mode: it reads the vendor app's notifications and re-serves them on
its LAN web service, which the PC polls. No Bluetooth is re-paired, no cloud
account is created, no server is run.

**Accepted trade-off:** notification capture is lossy — expect ~2-4 missed
readings/hour (~95% coverage). That is fine for analysis and narrative; it is
**not** an alarm channel. Keep using the vendor app's alarms for safety.

If you need complete 1-minute data later, switch to Juggluco direct-connect
(§7 Fallback A) — that seizes the sensor BLE and disables the vendor app's
alarms, so only do it when data completeness matters more than the vendor UI.

## 1. Confirm the sensor generation (do this first)

This route requires **AiDEX X** (15-day wear, 1-minute readings). Check the box
or the vendor app:

- Package says `AiDEX X` / `安耐糖 X`, 15-day wear, 1-min updates → **AiDEX X**, proceed.
- Package says `AiDEX` / `安耐糖`, 14-day wear, 5-min updates → **first-gen**,
  which broadcasts glucose in the clear over BLE and is **not** handled by this
  companion route; use a direct BLE collector instead (ask before proceeding).

Validate one live sensor end-to-end before enabling cron.

## 2. Prepare the vendor app

1. Keep the AiDEX vendor app paired and running as normal.
2. Ensure it shows a persistent glucose **notification** (lock/pin the
   notification so the system does not collapse or clear it).
3. Exempt the vendor app from battery optimisation and background sleeping, and
   add it to the auto-start allowlist (mainland ROMs kill background apps hard).

The vendor app remains the only app talking to the sensor over Bluetooth.

## 3. Install and configure xDrip+ in Companion mode

1. Install a current xDrip+ build from GitHub Releases. **Record the version and
   keep the APK**; once validated, do not auto-update (an app update can change
   notification parsing).
2. Hardware Data Source → **Companion App** (never configure any BLE source).
3. Grant xDrip the **notification access** permission when prompted.
4. Set xDrip's display unit to match the vendor app (**mmol/L**).
5. Inter-app settings → xDrip **Web Service**: enable it, enable **Open Web
   Service** (so the PC can reach it over the LAN, not just loopback), and set a
   strong **Web Service Secret**. The project reads that secret from Hermes
   `.env`, hashes it (SHA-1), and sends it as the `api-secret` header — never in
   the URL.
6. Exempt xDrip too from battery optimisation / background sleeping.
7. Confirm xDrip's main graph shows the same reading stream as the vendor app.

Put the phone and PC on the same trusted Wi-Fi, disable client/AP isolation for
those two devices, and create a DHCP reservation for the phone (a fixed IP such
as `192.168.1.25`). Plain HTTP is permitted by the project only for
loopback, private/link-local IPs, or `.local` hosts; for a guest/untrusted LAN
use a private VPN instead.

## 4. Configure Hermes

Edit the active Hermes `.env` without committing or sharing it — on Windows
`%LOCALAPPDATA%\hermes\.env`, on macOS/Linux `~/.hermes/.env` (whatever
`default_hermes_home()` resolves to):

```dotenv
CGM_AGENT_USER_ID=my-cgm-user
CGM_BRIDGE_KIND=xdrip
CGM_BRIDGE_URL=http://192.168.1.25:17580
CGM_BRIDGE_API_SECRET=<xDrip Web Service Secret>
CGM_BRIDGE_SOURCE=android:xdrip-companion
CGM_BRIDGE_COUNT=96
CGM_BRIDGE_EXPECTED_INTERVAL_MINUTES=1
CGM_BRIDGE_MAX_STALE_MINUTES=20   # companion notifications gap; widen to avoid false degraded
# Optional but recommended: freshness watchdog target (see §6). When set, the
# cron posts a PHI-free alert on a healthy<->stale boundary crossing.
#CGM_WEBHOOK_URL=https://hooks.example/cgm
```

`EXPECTED_INTERVAL_MINUTES=1` matches the AiDEX X cadence; `MAX_STALE_MINUTES=20`
absorbs companion-mode notification gaps so a normal 2-4/hour miss does not flap
the health status. Restart the Hermes gateway after changing `.env`.

## 5. Acceptance sequence

Run each gate in order (same commands on Windows PowerShell and macOS/Linux
shells):

```bash
python -m hermes_cgm_agent bridge-status
python -m hermes_cgm_agent bridge-poll
python -m hermes_cgm_agent bridge-poll
python -m hermes_cgm_agent context-build --user-id my-cgm-user
```

Acceptance requires:

- `bridge-status` is `ready`;
- at least one parsed reading and newest-reading age <= 20 minutes;
- no future clock skew;
- first poll inserts facts and the second poll reports duplicates rather than
  creating duplicate glucose points;
- `bridge_poll_completed` exists in `audit_logs`;
- Hermes `cgm_timeseries_get_realtime_snapshot` reads the same user/source from
  the canonical DB;
- a phone Wi-Fi disconnect and reconnect recovers without manual DB repair.

Companion-mode specific checks:

- spot-check that at least ~10 readings match the vendor app value at the same
  timestamp (companion mode should mirror, not diverge);
- clear the vendor app notification for ~5 minutes, then restore it — the next
  poll continues without inserting duplicate points;
- if `CGM_WEBHOOK_URL` is set, force a stale state (leave the notification
  cleared past `MAX_STALE_MINUTES`) and confirm the watchdog fires exactly one
  alert, then exactly one recovery alert when data resumes.

## 6. Continuous collection

After acceptance, create the deterministic job (use your local project path as
`--workdir`, e.g. `~/code/CGM-Agent` on macOS):

```bash
hermes cron add "*/1 * * * *" \
  --name cgm-android-bridge \
  --script cgm_bridge_poll.py \
  --no-agent \
  --workdir "$HOME/code/CGM-Agent"
```

The script performs no model call. It fetches a recent overlap window each
minute, relies on database uniqueness for idempotency, writes success/degraded/
failure audits, exits non-zero when readings are missing, stale, or have future
clock skew, and — when `CGM_WEBHOOK_URL` is set — runs the freshness watchdog.

### Freshness watchdog (defeats silent stalls)

A long-running personal collector fails silently: the phone gets killed at 3am
and nobody notices until the morning report is empty. The watchdog turns the
per-minute freshness check into an **edge-triggered** alert
(`services/sources/watchdog.py`, D064):

- It fires only when health crosses the healthy<->stale boundary — one alert
  when it goes stale, one when it recovers — so a persistent stall is announced
  once, not every minute.
- The alert is a PHI-free JSON POST to `CGM_WEBHOOK_URL`
  (`{alert, state, newest_reading_age_minutes, at}` — no glucose value, no user
  id), https-only and no-redirect, reusing the F5 webhook security properties.
- Prior state is read from the latest `bridge_poll_*` audit row — no extra
  table. Alerting is best-effort: a watchdog failure never breaks the poll.

Point `CGM_WEBHOOK_URL` at any endpoint you actually watch (a self-hosted
webhook, a chat relay, an SMS gateway). Without it the cron still exits non-zero
on stale data, but nothing pushes to you.

## 7. Fallbacks

### Fallback A — Juggluco direct-connect (data completeness over vendor UI)

When companion-mode loss is too high for your analysis needs, let Juggluco own
the sensor BLE directly (complete 1-minute data). **Cost:** the vendor app can
no longer connect, so its alarms stop — Juggluco's own alarms take over.

1. In the vendor app, unpair the sensor and force-stop it (only one app may own
   the BLE session; Juggluco requires the previous app unpaired and force-stopped).
2. Install a current Juggluco build (10.7.0+ for AiDEX X). Pair via the
   package data-matrix (Photo flow) or Exchange data → Glucose Meters → Find devices.
3. Allow Bluetooth/notifications; exempt Juggluco from battery optimisation.
4. Settings → Exchange data → Web server: enable, disable `Local only`, port
   `17580`, set a strong API secret.
5. `.env`: `CGM_BRIDGE_KIND=juggluco`, `CGM_BRIDGE_SOURCE=android:juggluco`.

### Fallback B — Nightscout relay (phone and PC on different networks)

When the phone and PC cannot share a LAN, configure xDrip/Juggluco to upload to
a private Nightscout instance, then set:

```dotenv
CGM_BRIDGE_KIND=nightscout
CGM_BRIDGE_URL=https://your-nightscout.example
CGM_BRIDGE_ACCESS_TOKEN=<read-only-token>
CGM_BRIDGE_SOURCE=nightscout:personal
```

Use a read-only token rather than a full administrative API secret when the
Nightscout deployment supports roles. Never enable public unauthenticated CGM
reads merely to simplify this collector.

### iOS

Deferred (D064): no companion-grade bridge exists on iOS (the OS forbids the
persistent background web server and free BLE scan the Android path relies on;
xDripSwift has no MicroTech support). The HTTP source layer is transport-
agnostic, so any future iOS source emitting the xDrip/Nightscout shape plugs in
with an `.env` change only.

## Source references

- <https://navid200.github.io/xDrip/docs/Follow/CompanionApp.html> — xDrip
  Companion App mode (notification capture, setup, limitations).
- <https://github.com/NightscoutFoundation/xDrip/discussions/3532> — LinX/AiDEX X
  companion support (added 2024-06).
- xDrip Local Web Services — `/sgv.json`, port 17580, Open Web Service, SHA-1
  `api-secret` header.
- <https://www.juggluco.nl/Juggluco/sensors/> — Juggluco AiDEX X/LinX support
  (Fallback A).
- <https://github.com/nightscout/cgm-remote-monitor> — Nightscout (Fallback B).
- <https://github.com/nightscout/cgm-remote-monitor> — optional remote relay.
