# CGM Analysis

Use this skill when the task requires Hermes to work with the CGM capability layer as a tool-backed health-data specialist.

## Purpose

- Prefer the `cgm` toolset over free-form reasoning when structured glucose data, events, reports, or RAG evidence are involved.
- Keep personal memory (`user_memory`) separate from authoritative knowledge (`authoritative_kb`).
- Treat generated reports and memory candidates as evidence-backed artifacts, not informal summaries.

## Expected tool usage

- `cgm_timeseries_get_points` for raw normalized point lookup
- `cgm_timeseries_get_aggregate` for TIR/TAR/TBR and summary metrics
- `cgm_events_create` / `cgm_events_confirm` for event capture
- `cgm_reports_generate` for controlled daily/weekly/doctor reports
- `cgm_rag_authoritative_search` for CGM knowledge-base lookup
- `cgm_hypothesis_update` for long-running behavior hypotheses
- `cgm_delivery_send` only after the payload is approved

## Rules

- Do not present `user_memory` as medical fact.
- When both data and memory are present, lead with measured data and use memory only as context.
- Prefer structured tool calls over ad hoc interpretation when a tool already exists.
