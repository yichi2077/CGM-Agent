"""Silent local health check used by the 2-hour Hermes cron job."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import sys
from pathlib import Path

_root = os.getenv("CGM_AGENT_PROJECT_ROOT")
if _root and str(Path(_root) / "src") not in sys.path:
    sys.path.insert(0, str(Path(_root) / "src"))

from hermes_cgm_agent.config import resolve_database_path
from hermes_cgm_agent.domain import DataScope
from hermes_cgm_agent.services.data import SQLiteCGMRepository
from hermes_cgm_agent.services.memory import CGMMemoryProvider
from hermes_cgm_agent.services.memory.l0_builder import L0ContextBuilder
from hermes_cgm_agent.services.rag import AuthoritativeRAGService
from hermes_cgm_agent.services.safety import assert_authoritative_quotes
from hermes_cgm_agent.storage.sqlite import SQLiteStore


def run_health_checks(db: Path, *, user_id: str, hermes_home: str | None) -> None:
    """Raise on a broken DB, L0/memory prefetch, or authoritative RAG path.

    This is intentionally read-only at the application layer.  It anchors the
    L0 check at the latest fact in the local DB, so an accelerated simulation
    does not appear unhealthy merely because wall-clock time is later.
    """

    store = SQLiteStore(db)
    store.initialize()
    repository = SQLiteCGMRepository(store)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) AS latest FROM glucose_points WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    latest_raw = str(row["latest"] or "") if row else ""
    if not latest_raw:
        raise RuntimeError("no glucose points for configured user")
    anchor = datetime.fromisoformat(latest_raw.replace("Z", "+00:00"))
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    anchor = anchor.astimezone(timezone.utc)
    points = repository.list_glucose_points(
        DataScope(
            user_id=user_id,
            window_start=anchor - timedelta(days=14),
            window_end=anchor,
        )
    )
    l0 = L0ContextBuilder(repository=repository, config=None).build(
        user_id=user_id,
        anchor_at=anchor,
    )
    if not points or not (0 < l0.window_summary.point_count <= len(points)):
        raise RuntimeError("L0 point count is outside the local fact window")
    if not l0.window_summary.data_coverage >= 0:
        raise RuntimeError("L0 data coverage is invalid")

    provider = CGMMemoryProvider(store, user_id=user_id)
    provider.initialize(
        session_id="cgm-health-check",
        user_id=user_id,
        hermes_home=hermes_home or "",
        platform="health",
        anchor_at=anchor,
    )
    retrieval_logger = logging.getLogger("hermes_cgm_agent.services.memory.retrieval")
    previous_level = retrieval_logger.level
    retrieval_logger.setLevel(logging.ERROR)
    try:
        prefetch = provider.prefetch("当前血糖、趋势和个人记忆", session_id="cgm-health-check")
    finally:
        retrieval_logger.setLevel(previous_level)
    if not prefetch.strip() or "[CGM state summary]" not in prefetch:
        raise RuntimeError("cgm_memory prefetch is empty or missing CGM state")

    documents = AuthoritativeRAGService().search("低血糖", top_k=1)
    if not documents:
        raise RuntimeError("authoritative RAG returned no document")
    for document in documents:
        result = assert_authoritative_quotes([document], document["text"], strict=True)
        if not result.ok:
            raise RuntimeError("authoritative RAG quote verification failed")


def main() -> int:
    db = resolve_database_path(os.getenv("HERMES_HOME"))
    key = db.parent / "storage.key"
    if not db.exists() or not key.exists():
        print("CGM health check failed: database or storage key is missing")
        return 1
    try:
        user_id = (os.getenv("CGM_AGENT_USER_ID") or "").strip()
        if not user_id:
            raise RuntimeError("CGM_AGENT_USER_ID is not configured")
        run_health_checks(db, user_id=user_id, hermes_home=os.getenv("HERMES_HOME"))
    except Exception as exc:  # noqa: BLE001 - cron must report a compact alert
        print(f"CGM health check failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
