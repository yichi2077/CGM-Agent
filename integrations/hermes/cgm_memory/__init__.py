from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent.memory_provider import MemoryProvider

ROOT_MARKER_NAME = "cgm-agent-project-root.txt"


def register(ctx: Any) -> None:
    ctx.register_memory_provider(HermesCGMMemoryProvider())


class HermesCGMMemoryProvider(MemoryProvider):
    """Hermes-facing wrapper around the project's CGM memory services."""

    def __init__(self) -> None:
        self._project_root = _ensure_project_import_path()
        self._store = None
        self._inner = None
        self._executor = None
        self._session_id = ""

    @property
    def name(self) -> str:
        return "cgm_memory"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        from hermes_cgm_agent.config import resolve_database_path
        from hermes_cgm_agent.services.audit import AuditService
        from hermes_cgm_agent.services.data import SQLiteCGMRepository
        from hermes_cgm_agent.services.memory import CGMMemoryProvider
        from hermes_cgm_agent.services.tools import ToolExecutor
        from hermes_cgm_agent.storage.sqlite import SQLiteStore

        # Resolve the DB path identically to the standalone `cgm` tool plugin so
        # tools and memory share one SQLite file (NEW-1). Hermes always passes
        # hermes_home here, but fall back to its own resolver for parity with the
        # tool plugin, which derives the same value via get_hermes_home().
        hermes_home = str(kwargs.get("hermes_home") or "").strip() or _runtime_hermes_home()
        db_path = resolve_database_path(hermes_home)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._store = SQLiteStore(db_path)
        self._store.initialize()
        self._inner = CGMMemoryProvider(self._store)
        self._executor = ToolExecutor(
            repository=SQLiteCGMRepository(self._store),
            audit_service=AuditService(self._store),
        )
        self._session_id = session_id
        self._inner.initialize(session_id=session_id, **kwargs)

    def system_prompt_block(self) -> str:
        self._require_initialized()
        return self._inner.system_prompt_block()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._require_initialized()
        return self._inner.prefetch(query, session_id=session_id)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self._require_initialized()
        self._inner.queue_prefetch(query, session_id=session_id)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self._require_initialized()
        self._inner.sync_turn(
            user_content,
            assistant_content,
            session_id=session_id,
            messages=messages,
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        # Single LLM-facing tool channel (D045 / F1 US3, Damocles W3): the standalone
        # `cgm` plugin registers ALL capability tools, including the four memory tools
        # (memory.list/delete/confirm/correct -> cgm_memory_*). The memory provider
        # therefore advertises NO tools, so each appears exactly once to the model.
        # The provider keeps its memory-provider duties (prefetch / sync_turn /
        # system_prompt_block) and can still execute a routed call via handle_tool_call.
        return []

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        self._require_initialized()
        session_id = str(kwargs.get("session_id") or self._session_id or "hermes-cgm-memory")
        response = self._executor.execute(
            tool_name=tool_name,
            arguments=args,
            session_id=session_id,
        )
        return json.dumps(response.to_dict(), ensure_ascii=True, sort_keys=True)

    def shutdown(self) -> None:
        if self._inner is not None:
            self._inner.shutdown()

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        self._require_initialized()
        self._inner.on_session_end(messages)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        self._session_id = new_session_id
        self._require_initialized()
        handler = getattr(self._inner, "on_session_switch", None)
        if callable(handler):
            handler(
                new_session_id,
                parent_session_id=parent_session_id,
                reset=reset,
                rewound=rewound,
                **kwargs,
            )

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        self._require_initialized()
        handler = getattr(self._inner, "on_pre_compress", None)
        if callable(handler):
            result = handler(messages)
            return str(result or "")
        return ""

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        self._require_initialized()
        handler = getattr(self._inner, "on_turn_start", None)
        if callable(handler):
            handler(turn_number, message, **kwargs)

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._require_initialized()
        handler = getattr(self._inner, "on_memory_write", None)
        if callable(handler):
            handler(action, target, content, metadata)

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs: Any,
    ) -> None:
        self._require_initialized()
        handler = getattr(self._inner, "on_delegation", None)
        if callable(handler):
            handler(task, result, child_session_id=child_session_id, **kwargs)

    def get_config_schema(self) -> list[dict[str, Any]]:
        return []

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        marker = Path(hermes_home).expanduser().resolve() / ROOT_MARKER_NAME
        if not marker.exists():
            marker.write_text(str(self._project_root), encoding="utf-8")

    def _require_initialized(self) -> None:
        if self._inner is None or self._executor is None or self._store is None:
            raise RuntimeError("HermesCGMMemoryProvider.initialize() must run before use.")


def _runtime_hermes_home() -> str:
    """Resolve the active HERMES_HOME the way Hermes does.

    Mirrors the helper in the standalone ``cgm`` tool plugin so both entry points
    derive an identical database path. Prefers Hermes's own resolver (which
    honors the ContextVar profile override that ``os.environ`` misses) and falls
    back to the env var when Hermes is not importable.
    """
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME", "")


def _ensure_project_import_path() -> Path:
    configured = os.environ.get("CGM_AGENT_PROJECT_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = _marker_project_root() or Path(__file__).resolve().parents[3]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    os.environ.setdefault("CGM_AGENT_PROJECT_ROOT", str(root))
    return root


def _marker_project_root() -> Path | None:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home).expanduser().resolve() if hermes_home else Path.home() / ".hermes"
    marker = base / ROOT_MARKER_NAME
    if not marker.exists():
        return None
    candidate = Path(marker.read_text(encoding="utf-8").strip()).expanduser()
    return candidate.resolve() if candidate.exists() else None
