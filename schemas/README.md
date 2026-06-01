# Schemas

This directory is reserved for project data contracts.

Current executable source of truth:

`src/hermes_cgm_agent/domain/cgm.py`

The first CGM domain contracts now exist as Pydantic models:

- `RawCGMRecord`
- `RawImportBatch`
- `ImportIssue`
- `GlucosePoint`
- `DeviceSession`
- `UserEvent`
- `GlucoseAggregate`
- `DataScope`
- `EvidenceRef`

These models are derived from the predev kit schemas in:

`docs/weitai-cgm-agent-predev-kit/schemas/`

JSON Schema exports should be generated from the Pydantic models when a
cross-process interface, Hermes plugin manifest, or external tool boundary
requires stable JSON contracts.
