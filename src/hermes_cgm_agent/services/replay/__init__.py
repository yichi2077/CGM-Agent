"""Replay engine (D051): accelerated historical-data playback for demos.

Drives the full product loop — points -> L0/consolidation -> tiered push
scheduling -> delivery — against a simulated clock, so the closed loop can be
exercised and demonstrated without a live CGM sensor. CLI-only (never a Hermes
tool): replay manipulates the simulated clock, and exposing that to the model
would hand it a scheduling-policy surface the constitution keeps out of reach.
"""

from hermes_cgm_agent.services.replay.engine import (
    ReplayConfig,
    ReplayReport,
    ReplayService,
)

__all__ = ["ReplayConfig", "ReplayReport", "ReplayService"]
