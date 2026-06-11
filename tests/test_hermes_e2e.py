from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_REPO = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent"
if not HERMES_REPO.exists():
    raise unittest.SkipTest(f"Hermes repo not found at {HERMES_REPO}")

sys.path.insert(0, str(HERMES_REPO))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _build_store(db_path: Path):
    from hermes_cgm_agent.storage.sqlite import SQLiteStore

    store = SQLiteStore(db_path)
    store.initialize()
    return store


def _build_executor(store):
    from hermes_cgm_agent.services.audit import AuditService
    from hermes_cgm_agent.services.data import SQLiteCGMRepository
    from hermes_cgm_agent.services.tools.executor import ToolExecutor

    repository = SQLiteCGMRepository(store)
    audit = AuditService(store)
    return ToolExecutor(repository=repository, audit_service=audit)


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


cgm_plugin = _load_module(
    "cgm_plugin",
    PROJECT_ROOT / "integrations" / "hermes" / "cgm" / "__init__.py",
)
cgm_memory_plugin = _load_module(
    "cgm_memory_plugin",
    PROJECT_ROOT / "integrations" / "hermes" / "cgm_memory" / "__init__.py",
)


PUSH_TICK_SCRIPT = """\
from hermes_cgm_agent.storage.sqlite import SQLiteStore
from hermes_cgm_agent.services.scheduling.scheduler import PushSchedulerService

store = SQLiteStore(db_path)
store.initialize()
service = PushSchedulerService(store=store)
result = service.push_tick(user_id=user_id, now=now)
print(result.to_dict())
"""


class TestAIAgentFullChain(unittest.TestCase):
    """Part A: Test AIAgent.chat() triggers CGM tool via LLM."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "e2e_agent.db"
        os.environ["CGM_AGENT_DB_PATH"] = str(self.db_path)

    def tearDown(self) -> None:
        os.environ.pop("CGM_AGENT_DB_PATH", None)
        self.temp_dir.cleanup()

    def test_aiagent_chat_triggers_push_tick(self) -> None:
        """AIAgent sends message -> LLM calls cgm_scheduling_push_tick -> result."""
        from run_agent import AIAgent

        agent = AIAgent(
            model="mimo-v2.5-pro",
            provider="xiaomi",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            enabled_toolsets=["cgm"],
            skip_memory=True,
            max_iterations=5,
            tool_delay=0.1,
        )

        response = agent.chat(
            "请调用 cgm_scheduling_push_tick 工具，user_id 为 e2e-aiagent-user"
        )

        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0, "AIAgent returned empty response")

    def test_aiagent_with_cgm_toolset_only(self) -> None:
        """AIAgent with only CGM tools can be created and chat."""
        from run_agent import AIAgent

        agent = AIAgent(
            model="mimo-v2.5-pro",
            provider="xiaomi",
            base_url="https://token-plan-cn.xiaomimimo.com/v1",
            enabled_toolsets=["cgm"],
            skip_memory=True,
            max_iterations=3,
            tool_delay=0.1,
        )

        response = agent.chat("你好，请介绍一下你自己")
        self.assertIsInstance(response, str)
        self.assertTrue(len(response) > 0)


if __name__ == "__main__":
    unittest.main()
