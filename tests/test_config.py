from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_cgm_agent.config import AppConfig, DEFAULT_DB_PATH, resolve_database_path


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


class AppConfigFromEnvTests(unittest.TestCase):
    """from_env() must resolve the SAME store the plugins use (F1 A1 / D045),
    not the hardcoded .runtime fallback, and keep the Fernet key beside the DB."""

    @staticmethod
    def _env_without(*keys: str) -> dict:
        env = dict(os.environ)
        for key in keys:
            env.pop(key, None)
        return env

    def test_from_env_uses_hermes_home_via_resolver(self) -> None:
        env = self._env_without("CGM_AGENT_DB_PATH", "CGM_AGENT_STORAGE_KEY_PATH")
        env["HERMES_HOME"] = "/home/user/.hermes"
        with patch.dict(os.environ, env, clear=True):
            cfg = AppConfig.from_env()
        self.assertEqual(cfg.database_path, resolve_database_path("/home/user/.hermes"))
        self.assertEqual(
            cfg.database_path,
            (Path("/home/user/.hermes") / "cgm-agent" / "app.db").resolve(),
        )

    def test_from_env_env_override_takes_precedence(self) -> None:
        env = self._env_without("CGM_AGENT_STORAGE_KEY_PATH")
        env["HERMES_HOME"] = "/home/user/.hermes"
        env["CGM_AGENT_DB_PATH"] = "/tmp/explicit/app.db"
        with patch.dict(os.environ, env, clear=True):
            cfg = AppConfig.from_env()
        self.assertEqual(cfg.database_path, Path("/tmp/explicit/app.db").expanduser().resolve())

    def test_storage_key_co_located_with_db_by_default(self) -> None:
        env = self._env_without("CGM_AGENT_DB_PATH", "CGM_AGENT_STORAGE_KEY_PATH")
        env["HERMES_HOME"] = "/home/user/.hermes"
        with patch.dict(os.environ, env, clear=True):
            cfg = AppConfig.from_env()
        self.assertEqual(cfg.resolved_storage_key_path.parent, cfg.database_path.parent)

    def test_warns_when_storage_key_outside_db_dir(self) -> None:
        env = self._env_without("CGM_AGENT_DB_PATH")
        env["HERMES_HOME"] = "/home/user/.hermes"
        env["CGM_AGENT_STORAGE_KEY_PATH"] = "/tmp/elsewhere/storage.key"
        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("hermes_cgm_agent.config", level="WARNING") as captured:
                AppConfig.from_env()
        self.assertTrue(any("storage_key_path" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
