"""Hermes no-agent cron entry for the Android CGM bridge."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> Path:
    configured = os.getenv("CGM_AGENT_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    hermes_home = Path(os.getenv("HERMES_HOME") or Path(os.getenv("LOCALAPPDATA", Path.home())) / "hermes")
    marker = hermes_home / "cgm-agent-project-root.txt"
    if marker.exists():
        return Path(marker.read_text(encoding="utf-8").strip()).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


root = _project_root()
src = root / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))

from hermes_cgm_agent.cli.bridge import _bridge_poll  # noqa: E402
from hermes_cgm_agent.config import AppConfig  # noqa: E402
from hermes_cgm_agent.services.sources import load_bridge_environment  # noqa: E402


def main() -> int:
    load_bridge_environment()
    return _bridge_poll(db_path=AppConfig.from_env().database_path, session_kind="cron")


if __name__ == "__main__":
    raise SystemExit(main())
