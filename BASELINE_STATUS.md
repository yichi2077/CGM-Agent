# Baseline Status

Last updated: 2026-05-31

This file records the G0-G7 hardening baseline.

Project root:

`C:\Users\postgres\Desktop\新建文件夹 (4)\hermes-cgm-agent`

Current baseline intent:

- Hermes CLI remains the main shell.
- This repository remains the CGM capability layer.
- Local CLI/API are engineering support surfaces.
- G0-G7 are being hardened before G8 memory/RAG development.

Known baseline facts:

- The project root was not a Git repository at the start of the G0-G7 reset audit.
- A local Git repository was initialized after the G0-G7 hardening files were laid down; no commit is required for this baseline record.
- The current hardening pass records source, examples, tests and docs on disk before entering G8.
- The runtime database remains local SQLite and can be overridden with `CGM_AGENT_DB_PATH`.

Manual verification commands:

```powershell
$env:PYTHONPATH='src'
python -m hermes_cgm_agent dev-status
python -m hermes_cgm_agent tools
python -m unittest discover -s tests
```
