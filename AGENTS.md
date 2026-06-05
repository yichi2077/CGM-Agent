# Hermes CGM Agent Project Context

This project builds a personal `CGM` blood glucose data AI agent on top of `Hermes Agent`.

Current priority:

1. Treat `Hermes CLI` as the main shell.
2. Keep this folder as the `CGM capability layer`.
3. Persist local sessions, outputs, and audit.
4. Build `CGM` modules behind explicit tool, storage, and workflow boundaries.

Engineering rules:

- Do not implement a separate general chat engine. Open-ended conversation goes through `Hermes`.
- Keep project code behind `AgentPlatform` style adapters so `Hermes` integration can be tested and replaced cleanly.
- Do not modify the local `Hermes` installation tree under `~/.hermes/hermes-agent`.
- Future `Hermes` memory providers must be user plugins or project services, not in-tree `Hermes` providers.
- Keep executable project code in the current repository root.
- Leave planning and source documents outside the Hermes install tree.
- The local project `CLI` and `API` are support surfaces, not the current main product shell.
- Next real product modules are `CGM` data, analytics, events, reports, memory and `RAG`.
- Keep outputs and docs recoverable on disk.
