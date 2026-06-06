from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_REPO = Path.home() / ".hermes" / "hermes-agent"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ToolCollector:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def register_tool(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _MemoryCollector:
    def __init__(self) -> None:
        self.providers: list[object] = []

    def register_memory_provider(self, provider: object) -> None:
        self.providers.append(provider)


class HermesPluginIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(HERMES_REPO))
        cls.cgm_plugin = _load_module(
            "test_cgm_plugin",
            PROJECT_ROOT / "integrations" / "hermes" / "cgm" / "__init__.py",
        )
        cls.cgm_memory_plugin = _load_module(
            "test_cgm_memory_plugin",
            PROJECT_ROOT / "integrations" / "hermes" / "cgm_memory" / "__init__.py",
        )

    def setUp(self) -> None:
        self.cgm_plugin._EXECUTOR_CACHE.clear()

    def test_cgm_tool_plugin_registers_toolset_entry(self) -> None:
        collector = _ToolCollector()

        self.cgm_plugin.register(collector)

        names = {call["name"] for call in collector.calls}
        self.assertEqual(
            names,
            {
                "cgm_reports_generate",
                "cgm_context_get_l0",
                "cgm_timeseries_get_points",
                "cgm_timeseries_get_aggregate",
                "cgm_events_create",
                "cgm_events_confirm",
                "cgm_memory_list",
                "cgm_memory_delete",
                "cgm_hypothesis_update",
                "cgm_rag_authoritative_search",
                "cgm_delivery_send",
                "cgm_data_dexcom_sync",
            },
        )
        for call in collector.calls:
            self.assertEqual(call["toolset"], "cgm")

    def test_cgm_tool_handler_executes_internal_tool_in_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.db"

            class _Response:
                def to_dict(self) -> dict[str, object]:
                    return {"status": "ok", "audit_id": "a1", "evidence_refs": []}

            class _Executor:
                def execute(self, *, tool_name: str, arguments: dict[str, object], session_id: str):
                    self.tool_name = tool_name
                    self.arguments = arguments
                    self.session_id = session_id
                    return _Response()

            fake_executor = _Executor()
            fake_store = Mock()

            with patch.dict(os.environ, {"CGM_AGENT_DB_PATH": str(db_path)}, clear=False):
                with patch.object(self.cgm_plugin, "_project_root", return_value=PROJECT_ROOT):
                    with patch.object(self.cgm_plugin, "_build_store", return_value=fake_store):
                        with patch.object(self.cgm_plugin, "_build_executor", return_value=fake_executor):
                            result = self.cgm_plugin._handle_tool_call(
                                "reports.generate",
                                {
                                    "session_id": "session-1",
                                    "user_id": "u1",
                                    "report_type": "daily",
                                },
                            )

        self.assertEqual(json.loads(result)["status"], "ok")
        self.assertEqual(fake_executor.tool_name, "reports.generate")
        self.assertEqual(fake_executor.arguments, {"user_id": "u1", "report_type": "daily"})
        self.assertEqual(fake_executor.session_id, "session-1")

    def test_cgm_tool_plugin_caches_executor_by_resolved_database_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "shared.db"
            fake_store = Mock()
            fake_executor = Mock()
            with patch("hermes_cgm_agent.config.resolve_database_path", return_value=db_path):
                with patch.object(self.cgm_plugin, "_build_store", return_value=fake_store) as build_store:
                    with patch.object(self.cgm_plugin, "_build_executor", return_value=fake_executor) as build_executor:
                        self.assertIs(self.cgm_plugin._get_executor(), fake_executor)
                        self.assertIs(self.cgm_plugin._get_executor(), fake_executor)

        build_store.assert_called_once_with(db_path)
        build_executor.assert_called_once_with(fake_store)

    def test_cgm_and_memory_plugins_share_database_path_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "shared.db"
            fake_store = Mock()
            fake_store.initialize.return_value = None
            memory_provider = self.cgm_memory_plugin.HermesCGMMemoryProvider()
            with patch("hermes_cgm_agent.config.resolve_database_path", return_value=db_path) as resolver:
                with patch("hermes_cgm_agent.storage.sqlite.SQLiteStore", return_value=fake_store):
                    memory_provider.initialize("session-1", hermes_home=temp_dir)

        resolver.assert_called_once_with(temp_dir)
        self.assertIs(memory_provider._store, fake_store)

    def test_memory_plugin_registers_provider_wrapper(self) -> None:
        collector = _MemoryCollector()

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.db"
            with patch.dict(os.environ, {"CGM_AGENT_DB_PATH": str(db_path)}, clear=False):
                self.cgm_memory_plugin.register(collector)

        self.assertEqual(len(collector.providers), 1)
        provider = collector.providers[0]
        self.assertEqual(provider.name, "cgm_memory")
        self.assertTrue(provider.is_available())
        schema_names = {schema["name"] for schema in provider.get_tool_schemas()}
        self.assertEqual(
            schema_names,
            {"memory.list", "memory.delete", "memory.confirm", "memory.correct"},
        )
