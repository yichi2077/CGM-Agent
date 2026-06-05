# Baseline Status

Last updated: 2026-05-31

This file records the G0-G7 hardening baseline.

Project root:

`/Users/yichizhang/code/CGM-Agent`

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

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent dev-status
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent tools
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests
```
