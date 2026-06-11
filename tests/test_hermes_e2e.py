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


if __name__ == "__main__":
    unittest.main()
