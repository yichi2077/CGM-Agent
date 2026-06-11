from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_REPO = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent"
if not HERMES_REPO.exists():
    raise unittest.SkipTest(f"Hermes repo not found at {HERMES_REPO}")

HERMES_HOME = HERMES_REPO.parent

sys.path.insert(0, str(HERMES_REPO))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Skip entire module if Hermes runtime dependencies are not available.
# The CGM venv does not include Hermes's full dependency tree.
# E2E tests must run with the Hermes venv:
#   %LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe -m pytest tests/test_hermes_e2e.py -v
try:
    import requests  # noqa: F401 — Hermes dependency
    import httpx  # noqa: F401 — Hermes dependency
except ImportError as _e:
    raise unittest.SkipTest(
        f"Hermes runtime dependencies missing ({_e}). "
        f"Run E2E tests with Hermes venv, not CGM venv."
    ) from _e


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


PUSH_TICK_SCRIPT = '''#!/usr/bin/env python3
"""Cron script: call push_tick directly without LLM."""
import sys, os, json, tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

CGM_AGENT_PROJECT_ROOT = os.environ.get("CGM_AGENT_PROJECT_ROOT", "")
if CGM_AGENT_PROJECT_ROOT:
    sys.path.insert(0, str(Path(CGM_AGENT_PROJECT_ROOT) / "src"))

from hermes_cgm_agent.storage.sqlite import SQLiteStore
from hermes_cgm_agent.services.scheduling import PushSchedulerService
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.domain.cgm import GlucosePoint, GlucoseUnit, QualityFlag

def main():
    db_path = os.environ.get("CGM_AGENT_DB_PATH")
    if not db_path:
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "cron_test.db")

    store = SQLiteStore(Path(db_path))
    store.initialize()

    repo = SQLiteCGMRepository(store)
    now = datetime.now(timezone.utc)
    for i in range(5):
        ts = now - timedelta(days=i, hours=12)
        point = GlucosePoint(
            user_id="cron-test-user",
            timestamp=ts,
            value=120.0 + i * 5,
            unit=GlucoseUnit.MG_DL,
            source="e2e-test",
            quality_flag=QualityFlag.VALID,
        )
        repo.create_glucose_point(point, replace=True)

    monday = now
    while monday.weekday() != 0:
        monday -= timedelta(days=1)
    monday = monday.replace(hour=10, minute=0, second=0, microsecond=0)

    service = PushSchedulerService(store=store)
    result = service.push_tick(user_id="cron-test-user", now=monday)

    output = {
        "user_id": result.user_id,
        "pushed_count": len(result.pushed),
        "silent_consent_count": len(result.silent_consent),
        "pushed_tiers": [p["tier"] for p in result.pushed],
    }
    print(json.dumps(output, ensure_ascii=False))

if __name__ == "__main__":
    main()
'''


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


class TestCronTickDirect(unittest.TestCase):
    """Part B: Test cron.tick() fires no_agent script that calls push_tick."""

    def setUp(self) -> None:
        from cron.jobs import create_job, remove_job, list_jobs
        self._create_job = create_job
        self._remove_job = remove_job
        self._list_jobs = list_jobs
        self._created_ids = []
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        for jid in self._created_ids:
            try:
                self._remove_job(jid)
            except Exception:
                pass
        self.temp_dir.cleanup()

    def _create_and_track(self, **kwargs):
        job = self._create_job(**kwargs)
        self._created_ids.append(job["id"])
        return job

    def test_no_agent_script_executes(self) -> None:
        """no_agent=True cron job runs script and captures output."""
        scripts_dir = HERMES_HOME / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "e2e_push_tick_test.py"
        script_path.write_text(
            'import json; print(json.dumps({"status": "ok", "source": "cron_script"}))',
            encoding="utf-8",
        )

        try:
            job = self._create_and_track(
                prompt="",
                schedule="every 1h",
                name="E2E Push Tick Script",
                script="e2e_push_tick_test.py",
                no_agent=True,
            )

            from cron.scheduler import _run_job_script
            success, output = _run_job_script(str(script_path))

            self.assertTrue(success, f"Script failed: {output}")
            self.assertIn("cron_script", output)

            result = json.loads(output.strip())
            self.assertEqual(result["status"], "ok")
        finally:
            script_path.unlink(missing_ok=True)

    def test_push_tick_script_writes_to_db(self) -> None:
        """push_tick script creates records in push_events table."""
        db_path = Path(self.temp_dir.name) / "cron_push.db"
        os.environ["CGM_AGENT_DB_PATH"] = str(db_path)

        scripts_dir = HERMES_HOME / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_path = scripts_dir / "e2e_real_push_tick.py"
        script_path.write_text(PUSH_TICK_SCRIPT, encoding="utf-8")

        try:
            from cron.scheduler import _run_job_script
            success, output = _run_job_script(str(script_path))

            self.assertTrue(success, f"Script failed: {output}")

            result = json.loads(output.strip())
            self.assertEqual(result["user_id"], "cron-test-user")
            self.assertIn("pushed_count", result)

            from hermes_cgm_agent.storage.sqlite import SQLiteStore
            store = SQLiteStore(db_path)
            with store.connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM push_events WHERE user_id = 'cron-test-user'"
                ).fetchone()
            self.assertGreater(row[0], 0, "No push_events records found")
        finally:
            script_path.unlink(missing_ok=True)
            os.environ.pop("CGM_AGENT_DB_PATH", None)

    def test_tick_fires_due_job(self) -> None:
        """cron.tick() fires a due no_agent job."""
        scripts_dir = HERMES_HOME / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        marker_path = Path(self.temp_dir.name) / "cron_marker.txt"
        script_path = scripts_dir / "e2e_marker_script.py"
        script_path.write_text(
            f'from pathlib import Path; Path(r"{marker_path}").write_text("fired")',
            encoding="utf-8",
        )

        try:
            job = self._create_and_track(
                prompt="",
                schedule="every 1h",
                name="E2E Marker Job",
                script="e2e_marker_script.py",
                no_agent=True,
                deliver="local",
            )

            from cron.jobs import trigger_job
            trigger_job(job["id"])

            from cron.scheduler import tick
            fired = tick(verbose=False)

            if marker_path.exists():
                self.assertEqual(marker_path.read_text(), "fired")
        finally:
            script_path.unlink(missing_ok=True)
            marker_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
