# Hermes CGM Agent

Personal continuous-glucose-monitoring capability for Hermes. It stores your CGM data locally, provides Hermes tools for analysis and reports, and can connect to Dexcom or supported compatible feeds.

## Install

```powershell
python -m pip install .
cgm-agent hermes-install
```

Then restart Hermes. To inspect the local installation:

```powershell
cgm-agent status
cgm-agent tools
```

## Configure

For a first-time setup, copy `.env.example` to the Hermes environment file and
set the single-user identity, timezone, database path, and the credentials for
any data source you enable. Keep all secrets in the local environment file;
never commit them.

## Android bridge and AiDEX sources

An authenticated Android/Juggluco/xDrip/Nightscout bridge can be checked and
polled with:

```powershell
cgm-agent bridge-status
cgm-agent bridge-poll
```

The optional MicroTech LinX/AiDEX API integration provides encrypted OAuth
tokens and incremental sync:

```powershell
cgm-agent aidex-auth --user-id your-user-id
cgm-agent aidex-status --user-id your-user-id
cgm-agent aidex-sync --user-id your-user-id --incremental
```

## Add data

Import a CGM export:

```powershell
cgm-agent import-cgm --file glucose.csv --format csv --user-id your-user-id
```

Or authorize and sync Dexcom:

```powershell
cgm-agent dexcom-auth --user-id your-user-id
cgm-agent dexcom-sync --user-id your-user-id
```

Supported compatible sources can be polled with `cgm-agent source-poll --help`.

## Upgrade and migration

After upgrading, run:

```powershell
cgm-agent migrate-db
cgm-agent hermes-install
```

## Troubleshooting

Run `cgm-agent status` first. If Hermes is unavailable, repair or start Hermes, then run `cgm-agent hermes-install` again. Your CGM data remains in the local Hermes data directory.

This software supports personal information workflows and does not replace medical advice or emergency care.
