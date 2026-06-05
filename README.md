# Hermes CGM Agent

Personal `CGM` AI agent capability layer built around `Hermes Agent`.

Current implementation priority:

1. Treat `Hermes CLI` as the main shell.
2. Keep this repository as the `CGM capability layer`.
3. Persist CGM data, memory, reports, and audit locally.
4. Build `CGM` modules behind tool and storage boundaries.
5. Keep open-ended chat delegated to `Hermes`.

## Quick Commands

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent status
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent dev-status
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent tools
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-version
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-install
```

Run tests:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m unittest discover -s tests
```

Memory retrieval runtime notes:

- Default runtime is dependency-free hashing retrieval. This avoids Hermes
  hanging on first-run `sentence-transformers` model downloads while loading
  project memory.
- To force hashing explicitly: `CGM_AGENT_USE_HASHING_EMBEDDER=1`
- To enable real semantic retrieval intentionally:
  `CGM_AGENT_ENABLE_SEMANTIC_RETRIEVAL=1`
- Optional custom model override:
  `CGM_AGENT_EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2`

## Structure

- `src/hermes_cgm_agent/domain/` - executable `CGM` domain contracts.
- `src/hermes_cgm_agent/hermes_plugins/` - installer for Hermes-side plugin activation.
- `src/hermes_cgm_agent/services/` - CGM analytics, data, memory, reports, RAG, tools, and audit services.
- `src/hermes_cgm_agent/services/analytics/` - reproducible `CGM` metric calculations.
- `src/hermes_cgm_agent/services/data/` - `CGM` repository service.
- `tests/fixtures/` - sample CGM CSV/JSON import files.
- `src/hermes_cgm_agent/services/tools/` - Hermes-facing `CGM` tool registry and executor.
- `src/hermes_cgm_agent/storage/` - `SQLite`-backed persistence.
- `integrations/hermes/cgm/` - Hermes in-process `cgm` tool plugin.
- `integrations/hermes/cgm_memory/` - Hermes external memory provider wrapper.
- `schemas/` - schema notes and future JSON Schema exports.
- `prompts/` - project prompt assets.
- `eval/` - evaluation samples and runners.

`Hermes` is expected to be installed on this machine. The adapter auto-discovers `hermes` from `PATH`, with per-platform fallbacks such as:

- macOS / Linux: `~/.hermes/bin/hermes`, `~/.local/bin/hermes`
- Windows: `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe`

To install or refresh the Hermes-side user plugins and activate the provider/toolset:

```bash
PYTHONPATH=src ~/.hermes/hermes-agent/venv/bin/python3 -m hermes_cgm_agent hermes-install
```

This command:

- installs `cgm` and `cgm_memory` into `~/.hermes/plugins/`
- writes a project-root marker under `~/.hermes/`
- enables the `cgm` plugin in Hermes
- activates `cgm_memory` as the external memory provider
- installs this project into Hermes' own runtime venv when available

Runtime data is stored under the project's `.runtime/` directory by default, for example:

`./.runtime/`

The SQLite file is created with `0600` permissions on Unix-like systems. Sensitive health payload columns are application-encrypted with a Fernet key stored at `.runtime/storage.key` by default. Override with `CGM_AGENT_STORAGE_KEY_PATH` or provide `CGM_AGENT_STORAGE_KEY` in managed deployments.
