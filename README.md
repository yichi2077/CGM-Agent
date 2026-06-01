# Hermes CGM Agent

Personal `CGM` AI agent capability layer built around `Hermes Agent`.

Current implementation priority:

1. Treat `Hermes CLI` as the main shell.
2. Keep this repository as the `CGM capability layer`.
3. Persist sessions, outputs, and audit locally.
4. Build `CGM` modules behind tool and storage boundaries.
5. Keep open-ended chat delegated to `Hermes`.

## Quick Commands

```powershell
python -m hermes_cgm_agent status
python -m hermes_cgm_agent dev-status
python -m hermes_cgm_agent tools
python -m hermes_cgm_agent hermes-version
python -m hermes_cgm_agent chat "Use one sentence to explain who you are"
python -m hermes_cgm_agent sessions --limit 10
```

Optional support surface:

```powershell
python -m hermes_cgm_agent serve
```

Run tests:

```powershell
python -m unittest discover -s tests
```

Manual review docs:

- `..\dveps\docs\hermes-cgm-agent-dev-plan\DEVELOPMENT_STATUS.md`
- `..\dveps\docs\hermes-cgm-agent-dev-plan\TESTING_AND_ACCEPTANCE_PLAN.md`
- `..\dveps\docs\hermes-cgm-agent-dev-plan\DEVELOPMENT_SCHEDULE.md`

## Structure

- `src/hermes_cgm_agent/api/` - optional local API support surface.
- `src/hermes_cgm_agent/domain/` - executable `CGM` domain contracts.
- `src/hermes_cgm_agent/platform/` - platform abstraction and `Hermes CLI` adapter.
- `src/hermes_cgm_agent/services/` - chat, sessions, audit, and future domain services.
- `src/hermes_cgm_agent/services/analytics/` - reproducible `CGM` metric calculations.
- `src/hermes_cgm_agent/services/data/` - `CGM` repository service.
- `tests/fixtures/` - sample CGM CSV/JSON import files.
- `src/hermes_cgm_agent/services/tools/` - Hermes-facing `CGM` tool registry and executor.
- `src/hermes_cgm_agent/storage/` - `SQLite`-backed persistence.
- `schemas/` - schema notes and future JSON Schema exports.
- `prompts/` - project prompt assets.
- `eval/` - evaluation samples and runners.
- `..\dveps\docs\hermes-cgm-agent-dev-plan\` - development planning documents.

`Hermes` is expected to be installed on this machine. The adapter auto-discovers `hermes.exe` from `PATH`, with a fallback to:

`C:\Users\postgres\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`

Runtime data is stored under:

`C:\Users\postgres\Desktop\新建文件夹 (4)\hermes-cgm-agent\.runtime\`
