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
