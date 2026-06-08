from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT_MARKER_NAME = "cgm-agent-project-root.txt"

def register(ctx: Any) -> None:
    _ensure_project_import_path()

    from hermes_cgm_agent.services.tools import build_default_tool_registry

    registry = build_default_tool_registry()
    for spec in registry.list(status="active"):
        # The `cgm` plugin is the single LLM-facing tool channel (D045 / F1 US3):
        # all active capability tools — including memory.confirm/correct — register
        # here. The cgm_memory provider no longer advertises a competing tool set
        # (its get_tool_schemas() returns []), so each tool appears exactly once.
        external_name = _external_tool_name(spec.name)
        ctx.register_tool(
            name=external_name,
            toolset="cgm",
            schema={
                "name": external_name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
            handler=_build_handler(spec.name),
            description=spec.description,
        )


def _build_handler(internal_name: str):
    def _handler(args: dict[str, Any], **kwargs: Any) -> str:
        return _handle_tool_call(internal_name, args, **kwargs)

    return _handler


def _handle_tool_call(internal_name: str, args: dict[str, Any], **kwargs: Any) -> str:
    _ensure_project_import_path()
    session_id = str(kwargs.get("session_id") or args.get("session_id") or "hermes-cgm-plugin-session")
    response = _get_executor().execute(
        tool_name=internal_name,
        arguments={key: value for key, value in args.items() if key != "session_id"},
        session_id=session_id,
    )
    return json.dumps(response.to_dict(), ensure_ascii=True, sort_keys=True)


def _project_root() -> Path:
    configured = os.environ.get("CGM_AGENT_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    marker = _marker_project_root()
    if marker is not None:
        return marker
    return Path(__file__).resolve().parents[3]


def _ensure_project_import_path() -> Path:
    root = _project_root()
    src = root / "src"
    os.environ.setdefault("CGM_AGENT_PROJECT_ROOT", str(root))
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    current = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = _join_pythonpath(src, current)
    return root


def _external_tool_name(internal_name: str) -> str:
    if internal_name == "reports.generate":
        return "cgm_reports_generate"
    return f"cgm_{internal_name.replace('.', '_')}"


# Module-level executor cache keyed by resolved DB path (NEW-2): Hermes can fire
# several CGM tool calls back-to-back, and rebuilding the SQLite store + running
# CREATE TABLE IF NOT EXISTS on every call adds latency and risks WAL contention.
_EXECUTOR_CACHE: dict[str, Any] = {}


def _runtime_hermes_home() -> str:
    """Resolve the active HERMES_HOME the way Hermes does.

    Standalone tool handlers do NOT receive ``hermes_home`` in kwargs (only the
    memory provider's ``initialize()`` does), and the active home may be set via
    a ContextVar override that never touches ``os.environ``. Importing Hermes's
    own resolver keeps this plugin's DB path in lockstep with ``cgm_memory``.
    Falls back to the env var when Hermes is not importable (e.g. unit tests).
    """
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME", "")


def _get_executor() -> Any:
    from hermes_cgm_agent.config import resolve_database_path

    db_path = resolve_database_path(_runtime_hermes_home())
    key = str(db_path)
    executor = _EXECUTOR_CACHE.get(key)
    if executor is None:
        executor = _build_executor(_build_store(db_path))
        _EXECUTOR_CACHE[key] = executor
    return executor


def _build_store(db_path: Path):
    from hermes_cgm_agent.storage.sqlite import SQLiteStore

    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore(db_path)
    store.initialize()
    return store


def _build_executor(store: Any):
    from hermes_cgm_agent.services.audit import AuditService
    from hermes_cgm_agent.services.data import SQLiteCGMRepository
    from hermes_cgm_agent.services.tools import ToolExecutor

    return ToolExecutor(
        repository=SQLiteCGMRepository(store),
        audit_service=AuditService(store),
    )


def _marker_project_root() -> Path | None:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home).expanduser().resolve() if hermes_home else Path.home() / ".hermes"
    marker = base / ROOT_MARKER_NAME
    if not marker.exists():
        return None
    candidate = Path(marker.read_text(encoding="utf-8").strip()).expanduser()
    return candidate.resolve() if candidate.exists() else None


def _join_pythonpath(src_path: Path, current: str | None) -> str:
    if not current:
        return str(src_path)
    parts = [str(src_path), *[part for part in current.split(os.pathsep) if part]]
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        unique.append(part)
    return os.pathsep.join(unique)
