"""G2: cgm plugin executor-cache keying/invalidation, runnable WITHOUT Hermes.

`tests/test_hermes_plugin_integration.py` covers the dual-plugin contract but
skips entirely when the Hermes repo is absent (module import of cgm_memory
needs Hermes internals). The standalone `cgm` tool plugin has no such import,
so its executor cache — the piece that decides which SQLite store a Hermes
tool call hits — is tested here in every environment.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_cgm_plugin():
    path = PROJECT_ROOT / "integrations" / "hermes" / "cgm" / "__init__.py"
    spec = importlib.util.spec_from_file_location("cache_test_cgm_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecutorCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _load_cgm_plugin()
        self.plugin._EXECUTOR_CACHE.clear()
        self.addCleanup(self.plugin._EXECUTOR_CACHE.clear)

    def test_same_resolved_path_reuses_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "a.db"
            with patch("hermes_cgm_agent.config.resolve_database_path", return_value=db_path):
                with patch.object(self.plugin, "_build_store", return_value=Mock()) as build_store:
                    with patch.object(self.plugin, "_build_executor", side_effect=lambda s: Mock()):
                        first = self.plugin._get_executor()
                        second = self.plugin._get_executor()
        self.assertIs(first, second)
        build_store.assert_called_once()

    def test_changed_resolved_path_builds_new_executor(self) -> None:
        # The active HERMES_HOME (and thus the resolved DB path) can change
        # between tool calls — e.g. a profile switch or an operator override.
        # A stale cache hit would silently route reads/writes to the OLD store:
        # exactly the split-brain class F1/A1 eliminated.
        with tempfile.TemporaryDirectory() as temp_dir:
            path_a = Path(temp_dir) / "a.db"
            path_b = Path(temp_dir) / "b.db"
            with patch.object(self.plugin, "_build_store", side_effect=lambda p: Mock()):
                with patch.object(self.plugin, "_build_executor", side_effect=lambda s: Mock()):
                    with patch(
                        "hermes_cgm_agent.config.resolve_database_path", return_value=path_a
                    ):
                        executor_a = self.plugin._get_executor()
                    with patch(
                        "hermes_cgm_agent.config.resolve_database_path", return_value=path_b
                    ):
                        executor_b = self.plugin._get_executor()
                    with patch(
                        "hermes_cgm_agent.config.resolve_database_path", return_value=path_a
                    ):
                        executor_a_again = self.plugin._get_executor()
        self.assertIsNot(executor_a, executor_b)
        self.assertIs(executor_a, executor_a_again)


if __name__ == "__main__":
    unittest.main()
