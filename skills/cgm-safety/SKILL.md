# CGM Safety

Use this skill when CGM outputs may affect health interpretation, clinician communication, or external delivery.

## Safety posture

- Do not infer diagnosis from CGM memory alone.
- Keep explicit separation between:
  - measured glucose data
  - user-confirmed events
  - pending memory candidates
  - authoritative CGM knowledge-base results

## Delivery constraints

- `cgm_delivery_send` may write local manifests immediately.
- Remote channels such as `email` and `webhook` must be treated as queued until a configured delivery path exists.

## Response constraints

- Cite evidence where possible.
- If the data window is incomplete or sparse, say so directly.
- If a conclusion depends on a memory candidate that has not been confirmed, label it as unconfirmed.
