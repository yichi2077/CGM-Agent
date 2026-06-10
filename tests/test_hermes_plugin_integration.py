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
if not HERMES_REPO.exists():
    appdata_local = os.environ.get("LOCALAPPDATA")
    if appdata_local:
        candidate = Path(appdata_local) / "hermes" / "hermes-agent"
        if candidate.exists():
            HERMES_REPO = candidate


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
                "cgm_memory_confirm",
                "cgm_memory_correct",
                "cgm_hypothesis_update",
                "cgm_rag_authoritative_search",
                "cgm_rag_verify_quotes",
                "cgm_delivery_send",
                "cgm_data_dexcom_sync",
            },
        )
        for call in collector.calls:
            self.assertEqual(call["toolset"], "cgm")
        memory_list = next(call for call in collector.calls if call["name"] == "cgm_memory_list")
        properties = memory_list["schema"]["parameters"]["properties"]
        self.assertIn("candidates", properties["layer"]["enum"])
        self.assertEqual(
            properties["candidate_status"]["enum"],
            ["pending", "accepted", "rejected", "all"],
        )

    def test_plugin_yaml_provides_tools_matches_runtime_registration(self) -> None:
        # R2-1: the static plugin.yaml manifest must declare exactly the tools the
        # plugin registers at runtime. register() derives tools dynamically from
        # the active registry, so a new tool silently drifts the manifest. This
        # guard locks declaration == reality (no YAML dependency: manual parse).
        collector = _ToolCollector()
        self.cgm_plugin.register(collector)
        runtime_names = {call["name"] for call in collector.calls}

        manifest = (
            PROJECT_ROOT / "integrations" / "hermes" / "cgm" / "plugin.yaml"
        ).read_text(encoding="utf-8")
        declared: set[str] = set()
        in_block = False
        for line in manifest.splitlines():
            if line.strip().startswith("provides_tools:"):
                in_block = True
                continue
            if in_block:
                stripped = line.strip()
                if stripped.startswith("- "):
                    declared.add(stripped[2:].strip())
                elif stripped and not line.startswith((" ", "\t")):
                    break  # next top-level key ends the list
        self.assertEqual(declared, runtime_names)

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
        # Single LLM-facing channel (F1 US3 / Damocles W3): the provider advertises
        # NO tools; the cgm plugin owns tool registration. The candidate layer/status
        # schema is asserted via cgm_memory_list in the plugin registration test.
        self.assertEqual(provider.get_tool_schemas(), [])

    def test_memory_tools_have_a_single_llm_facing_registration(self) -> None:
        # C4 / Damocles W3: memory.confirm/correct are reachable via the cgm plugin
        # exactly once, and the memory provider advertises no competing tool set.
        collector = _ToolCollector()
        self.cgm_plugin.register(collector)
        names = [call["name"] for call in collector.calls]
        self.assertEqual(names.count("cgm_memory_confirm"), 1)
        self.assertEqual(names.count("cgm_memory_correct"), 1)

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.db"
            with patch.dict(os.environ, {"CGM_AGENT_DB_PATH": str(db_path)}, clear=False):
                mem_collector = _MemoryCollector()
                self.cgm_memory_plugin.register(mem_collector)
        self.assertEqual(mem_collector.providers[0].get_tool_schemas(), [])


class RuntimePathDataVisibilityTests(unittest.TestCase):
    """SC-001: data written at the resolved store path is exactly what the CLI
    config (AppConfig.from_env) points at, and is readable back — no split-brain."""

    def test_cli_config_points_at_store_where_data_lives(self) -> None:
        from datetime import datetime, timezone

        from hermes_cgm_agent.config import AppConfig, resolve_database_path
        from hermes_cgm_agent.domain import UserEvent
        from hermes_cgm_agent.services.data import SQLiteCGMRepository
        from hermes_cgm_agent.storage.sqlite import SQLiteStore

        with tempfile.TemporaryDirectory() as home:
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in ("CGM_AGENT_DB_PATH", "CGM_AGENT_STORAGE_KEY_PATH")
            }
            env["HERMES_HOME"] = home
            with patch.dict(os.environ, env, clear=True):
                target = resolve_database_path(home)
                store = SQLiteStore(target)
                store.initialize()
                SQLiteCGMRepository(store).create_user_event(
                    UserEvent(
                        event_id="evt-visible",
                        user_id="u1",
                        type="meal",
                        ts_start=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
                        created_by="user",
                        user_confirmed=True,
                    )
                )
                config = AppConfig.from_env()
                self.assertEqual(config.database_path, target)
                read_back = SQLiteCGMRepository(
                    SQLiteStore(config.database_path)
                ).get_user_event("evt-visible")

        self.assertEqual(read_back.user_id, "u1")
        self.assertEqual(read_back.event_type, "meal")


if __name__ == "__main__":
    unittest.main()
