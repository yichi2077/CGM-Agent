from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.config import DEFAULT_DB_PATH, resolve_database_path


class ResolveDatabasePathTests(unittest.TestCase):
    """The single source of truth that keeps the cgm and cgm_memory plugins on
    one SQLite file (NEW-1). Precedence: env override > hermes_home > default."""

    def test_env_override_wins_over_everything(self) -> None:
        with patch.dict(os.environ, {"CGM_AGENT_DB_PATH": "/tmp/explicit/app.db"}, clear=False):
            resolved = resolve_database_path("/some/hermes/home")
        self.assertEqual(resolved, Path("/tmp/explicit/app.db").expanduser().resolve())

    def test_hermes_home_scopes_under_cgm_agent_subdir(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CGM_AGENT_DB_PATH", None)
            resolved = resolve_database_path("/home/user/.hermes")
        self.assertEqual(resolved, (Path("/home/user/.hermes") / "cgm-agent" / "app.db").resolve())

    def test_falls_back_to_default_without_hermes_home(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CGM_AGENT_DB_PATH", None)
            for empty in (None, "", "   "):
                self.assertEqual(resolve_database_path(empty), Path(DEFAULT_DB_PATH))

    def test_cgm_and_memory_resolution_match_for_same_inputs(self) -> None:
        # Both plugins call resolve_database_path with the same hermes_home, so
        # identical inputs must produce one path — this is the anti-split-brain
        # invariant the integration relies on.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CGM_AGENT_DB_PATH", None)
            tool_side = resolve_database_path("/home/user/.hermes")
            memory_side = resolve_database_path("/home/user/.hermes")
        self.assertEqual(tool_side, memory_side)


if __name__ == "__main__":
    unittest.main()
