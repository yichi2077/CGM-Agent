"""Module entry point for ``python -m hermes_cgm_agent.cli``."""

from __future__ import annotations

from hermes_cgm_agent.cli.dispatch import main


if __name__ == "__main__":
    raise SystemExit(main())
