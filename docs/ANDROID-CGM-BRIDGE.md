# Android CGM Bridge Runbook

## Chosen production route

```text
MicroTech LinX/AiDEX-X sensor
  -> Android phone Bluetooth
  -> Juggluco xDrip-compatible web server
  -> trusted home LAN
  -> cgm_bridge_poll.py (Hermes no-agent cron)
  -> canonical app.db
  -> analytics, detected events, memory and Hermes CGM tools
```

Juggluco is the first choice because its current documentation explicitly lists
LinX/AiDEX-X sensor support and its built-in web server implements both xDrip
`/sgv.json` and Nightscout `/api/v1/entries/sgv.json`. This avoids a vendor API
entitlement and avoids operating a separate Nightscout server on the same LAN.

Use xDrip+ only when it can already receive this exact sensor through a proven
collector/companion path. Add Nightscout only when the phone must relay data to
the PC across the Internet or different networks.

## 1. Confirm the exact sensor

Record whether the package says `LinX`, `AiDEX-X`, or another AiDEX generation.
Juggluco 10.7.0+ documents AiDEX-X support and 10.7.2+ documents LinX support.
Do not assume an older transmitter is protocol-compatible; validate one live
sensor before moving the production identity or enabling cron.

## 2. Pair Android to the sensor

1. Install a current Juggluco build from its official site.
2. If another app/phone owns the BLE connection, stop it and unpair the sensor.
   Juggluco's sensor guide states that an AiDEX-X previously connected to another
   phone must be unpaired and the previous app force-stopped.
3. In Juggluco use the Photo/data-matrix flow, or Exchange data -> Glucose
   Meters -> Find devices and select the device whose serial matches the package.
4. Allow Bluetooth/Nearby devices and notifications.
5. Exempt Juggluco from Android battery optimisation and background sleeping.
6. Wait until several live readings have real timestamps before enabling export.

Only one app should directly own the sensor BLE session unless the exact sensor
and apps have proven otherwise. The PC never connects to the transmitter; it
polls the phone's HTTP service.

## 3. Enable the phone web server

In Juggluco open Settings -> Exchange data -> Web server:

1. Enable the web/xDrip server.
2. Disable `Local only` so the PC can access it on the home LAN.
3. Keep the standard port `17580` unless the phone configuration requires a
   different one.
4. Set a strong API secret.
5. Do not place the secret in the URL path. The project reads it from Hermes
   `.env`, hashes it, and sends the `api-secret` header.

Put the phone and PC on the same trusted Wi-Fi, disable client/AP isolation for
those two devices, and create a DHCP reservation for the phone. Prefer a fixed
IP such as `192.168.1.25` over a changing address. Plain HTTP is permitted by the
project only for loopback, private/link-local IPs, or `.local` hosts. For a guest
or untrusted LAN, use Juggluco HTTPS or a private VPN instead.

## 4. Configure Hermes

Edit `%LOCALAPPDATA%\hermes\.env` without committing or sharing it:

```dotenv
CGM_AGENT_USER_ID=my-cgm-user
CGM_BRIDGE_KIND=juggluco
CGM_BRIDGE_URL=http://192.168.1.25:17580
CGM_BRIDGE_API_SECRET=<the-phone-web-server-secret>
CGM_BRIDGE_SOURCE=android:juggluco
CGM_BRIDGE_COUNT=48
CGM_BRIDGE_EXPECTED_INTERVAL_MINUTES=5
CGM_BRIDGE_MAX_STALE_MINUTES=12
```

Restart the Hermes gateway after changing `.env`.

## 5. Acceptance sequence

Run each gate in order:

```powershell
python -m hermes_cgm_agent bridge-status
python -m hermes_cgm_agent bridge-poll
python -m hermes_cgm_agent bridge-poll
python -m hermes_cgm_agent context-build --user-id my-cgm-user
```

Acceptance requires:

- `bridge-status` is `ready`;
- at least one parsed reading and newest-reading age <= 12 minutes;
- no future clock skew;
- first poll inserts facts and the second poll reports duplicates rather than
  creating duplicate glucose points;
- `bridge_poll_completed` exists in `audit_logs`;
- Hermes `cgm_timeseries_get_realtime_snapshot` reads the same user/source from
  the canonical DB;
- a phone Wi-Fi disconnect and reconnect recovers without manual DB repair.

## 6. Continuous collection

After acceptance, create the deterministic job:

```powershell
hermes cron add "*/1 * * * *" `
  --name cgm-android-bridge `
  --script cgm_bridge_poll.py `
  --no-agent `
  --workdir "E:\字幕组测试\CGM-Agent"
```

The script performs no model call. It fetches a recent overlap window each
minute, relies on database uniqueness for idempotency, writes success/degraded/
failure audits, and exits non-zero when readings are missing, stale, or have
future clock skew.

## 7. Nightscout fallback

When the phone and PC cannot share a LAN, configure Juggluco/xDrip to upload to a
private Nightscout instance, then set:

```dotenv
CGM_BRIDGE_KIND=nightscout
CGM_BRIDGE_URL=https://your-nightscout.example
CGM_BRIDGE_ACCESS_TOKEN=<read-only-token>
CGM_BRIDGE_SOURCE=nightscout:personal
```

Use a read-only token rather than a full administrative API secret when the
Nightscout deployment supports roles. Never enable public unauthenticated CGM
reads merely to simplify this collector.

## Source references

- <https://www.juggluco.nl/> — current sensor support, including LinX/AiDEX-X.
- <https://www.juggluco.nl/Juggluco/sensors/> — AiDEX-X/LinX pairing notes.
- <https://www.juggluco.nl/Juggluco/webserver.html> — web server, API secret,
  xDrip and Nightscout-compatible endpoints.
- <https://github.com/NightscoutFoundation/xDrip> — alternative Android hub.
- <https://github.com/nightscout/cgm-remote-monitor> — optional remote relay.
